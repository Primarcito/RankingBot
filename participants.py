import re
import unicodedata
from difflib import SequenceMatcher

import discord

from database import find_scout_alias, get_all_scouts, get_scout_aliases


PLUS_NAME_RE = re.compile(r"(?<!\S)\+([\w.-]{2,32})", re.UNICODE)
MENTION_RE = re.compile(r"<@!?(\d+)>")
ID_RE = re.compile(r"\b\d{15,25}\b")


def normalize_name(name: str) -> str:
    text = unicodedata.normalize("NFKD", str(name or ""))
    return "".join(ch.lower() for ch in text if ch.isalnum())


def extract_plus_names(text: str) -> list[str]:
    return PLUS_NAME_RE.findall(text or "")


def contains_participant_reference(text: str):
    text = text or ""
    return bool(PLUS_NAME_RE.search(text) or MENTION_RE.search(text) or ID_RE.search(text))


def extract_manual_names(text: str) -> list[str]:
    text = text or ""
    names = []
    consumed_spans = []

    for regex in (MENTION_RE, ID_RE, PLUS_NAME_RE):
        for match in regex.finditer(text):
            names.append(match.group(1) if regex is not ID_RE else match.group(0))
            consumed_spans.append(match.span())

    remaining = remove_spans(text, consumed_spans)
    for chunk in re.split(r"[\n,;]+", remaining):
        chunk = chunk.strip().strip("@+")
        if not chunk:
            continue
        if " " in chunk:
            names.extend(part.strip().strip("@+") for part in chunk.split() if part.strip())
        else:
            names.append(chunk)

    return dedupe_raw_names(name for name in names if len(normalize_name(name)) >= 2)


def remove_spans(text: str, spans: list[tuple[int, int]]) -> str:
    chars = list(text)
    for start, end in spans:
        for index in range(start, end):
            chars[index] = " "
    return "".join(chars)


def dedupe_raw_names(names) -> list[str]:
    result = []
    seen = set()
    for name in names:
        key = normalize_name(name)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(name)
    return result


async def resolve_plus_names(guild: discord.Guild, text: str, excluded_user_ids: set[str] | None = None):
    return await resolve_names(guild, extract_plus_names(text), excluded_user_ids)


async def resolve_manual_names(guild: discord.Guild, text: str, excluded_user_ids: set[str] | None = None):
    return await resolve_names(guild, extract_manual_names(text), excluded_user_ids)


async def resolve_names(guild: discord.Guild, raw_names: list[str], excluded_user_ids: set[str] | None = None):
    excluded_user_ids = {str(user_id) for user_id in (excluded_user_ids or set())}
    participants = {}
    suggestions = []
    unresolved = []

    for raw_name in dedupe_raw_names(raw_names):
        exact = await resolve_exact_participant(guild, raw_name, excluded_user_ids)
        if exact:
            user_id, display_name = exact
            participants[user_id] = display_name
            excluded_user_ids.add(user_id)
            continue

        suggestion = await suggest_participant_for_name(guild, raw_name, excluded_user_ids)
        if suggestion:
            suggestions.append(suggestion)
            continue

        unresolved.append(raw_name)

    return list(participants.items()), unresolved, dedupe_suggestions(suggestions)


async def resolve_exact_participant(guild: discord.Guild, raw_name: str, excluded_user_ids: set[str]):
    member = await resolve_member_by_id(guild, raw_name)
    if member and not member.bot and str(member.id) not in excluded_user_ids:
        return str(member.id), member.display_name

    # Registered aliases are deliberate mappings and should beat live display names.
    alias = find_scout_alias(raw_name)
    if alias and str(alias[0]) not in excluded_user_ids:
        return str(alias[0]), alias[1]

    found = find_known_scout_by_name(raw_name)
    if found and str(found[0]) not in excluded_user_ids:
        return str(found[0]), found[1]

    member = resolve_member_by_name(guild, raw_name) or await query_exact_member_by_name(guild, raw_name)
    if member and not member.bot and str(member.id) not in excluded_user_ids:
        return str(member.id), member.display_name

    return None


async def resolve_member_by_id(guild: discord.Guild, raw_name: str):
    raw_name = str(raw_name or "").strip()
    if not raw_name.isdigit():
        return None

    member = guild.get_member(int(raw_name))
    if member:
        return member

    try:
        return await guild.fetch_member(int(raw_name))
    except (discord.HTTPException, discord.Forbidden, discord.NotFound):
        return None


def resolve_member_by_name(guild: discord.Guild, name: str):
    target = normalize_name(name)
    for member in guild.members:
        names = [member.name, member.display_name, member.global_name or ""]
        if any(normalize_name(candidate) == target for candidate in names):
            return member
    return None


async def query_exact_member_by_name(guild: discord.Guild, name: str):
    try:
        members = await guild.query_members(name, limit=10, cache=True)
    except (discord.HTTPException, discord.Forbidden, AttributeError):
        return None

    target = normalize_name(name)
    for member in members:
        names = [member.name, member.display_name, member.global_name or ""]
        if any(normalize_name(candidate) == target for candidate in names):
            return member
    return None


async def suggest_participant_for_name(guild: discord.Guild, raw_name: str, excluded_user_ids: set[str]):
    target = normalize_name(raw_name)
    if not target:
        return None

    candidates = {}
    add_member_candidates(candidates, target, guild.members, excluded_user_ids)

    try:
        queried_members = await guild.query_members(raw_name, limit=20, cache=True)
    except (discord.HTTPException, discord.Forbidden, AttributeError):
        queried_members = []
    add_member_candidates(candidates, target, queried_members, excluded_user_ids)
    add_alias_candidates(candidates, target, excluded_user_ids)
    add_known_scout_candidates(candidates, target, excluded_user_ids)

    if not candidates:
        return None

    best = max(candidates.values(), key=lambda item: item["score"])
    if best["score"] < score_threshold(target):
        return None

    return {
        "raw": raw_name,
        "user_id": best["user_id"],
        "display_name": best["display_name"],
        "score": best["score"],
        "source": best["source"],
    }


def add_member_candidates(candidates: dict, target: str, members, excluded_user_ids: set[str]):
    for member in members:
        if member.bot or str(member.id) in excluded_user_ids:
            continue

        names = [member.name, member.display_name, member.global_name or ""]
        score = max(score_name_match(target, candidate) for candidate in names)
        upsert_candidate(candidates, str(member.id), member.display_name, score, "Discord")


def add_known_scout_candidates(candidates: dict, target: str, excluded_user_ids: set[str]):
    for row in get_all_scouts():
        user_id, username = str(row[0]), row[1]
        if user_id in excluded_user_ids:
            continue
        score = score_name_match(target, username)
        upsert_candidate(candidates, user_id, username, score, "Ranking")


def add_alias_candidates(candidates: dict, target: str, excluded_user_ids: set[str]):
    for user_id, username, alias in get_scout_aliases():
        user_id = str(user_id)
        if user_id in excluded_user_ids:
            continue
        score = score_name_match(target, alias)
        upsert_candidate(candidates, user_id, username, score, f"Alt: {alias}")


def upsert_candidate(candidates: dict, user_id: str, display_name: str, score: int, source: str):
    if score <= 0:
        return
    current = candidates.get(user_id)
    if current and current["score"] >= score:
        return
    candidates[user_id] = {
        "user_id": user_id,
        "display_name": display_name,
        "score": score,
        "source": source,
    }


def find_known_scout_by_name(name: str):
    target = normalize_name(name)
    for user_id, username, *_ in get_all_scouts():
        if normalize_name(username) == target:
            return user_id, username
    return None


def score_name_match(target: str, candidate: str):
    candidate = normalize_name(candidate)
    if not target or not candidate:
        return 0
    if target == candidate:
        return 100
    if candidate.startswith(target):
        return 94
    if target.startswith(candidate) and len(candidate) >= 3:
        return 90
    if len(target) >= 4 and (target in candidate or candidate in target):
        return 86
    return round(SequenceMatcher(None, target, candidate).ratio() * 100)


def score_threshold(target: str):
    if len(target) <= 3:
        return 88
    if len(target) <= 5:
        return 78
    return 72


def dedupe_suggestions(suggestions: list[dict]):
    deduped = []
    seen = set()
    for suggestion in suggestions:
        key = (suggestion["raw"].lower(), suggestion["user_id"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(suggestion)
    return deduped[:25]


def format_participant_suggestions(suggestions: list[dict]):
    lines = []
    for suggestion in suggestions[:10]:
        lines.append(
            f"`+{suggestion['raw']}` -> <@{suggestion['user_id']}> "
            f"({suggestion['score']}%, {suggestion['source']})"
        )
    if len(suggestions) > 10:
        lines.append(f"... y {len(suggestions) - 10} mas")
    return "\n".join(lines)
