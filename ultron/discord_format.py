"""Discord embed helpers for richer slash replies."""

from __future__ import annotations

import discord


def embed_time_summary(
    *,
    user_label: str,
    today_h: float,
    week_h: float,
    last7_h: float,
    last24_h: float,
    timezone_name: str,
) -> discord.Embed:
    e = discord.Embed(
        title=f"Redmine time — {user_label}",
        color=0x3498DB,
        description=(
            f"Hours from fetched time entries (**config timezone:** `{timezone_name}`).\n"
            "**Last 24 h** uses each entry's **created_on** (UTC); other rows use **spent_on** dates."
        ),
    )
    e.add_field(name="Today (spent_on)", value=f"{today_h:g} h", inline=True)
    e.add_field(name="This week (Mon–today)", value=f"{week_h:g} h", inline=True)
    e.add_field(name="Last 7 days (spent_on)", value=f"{last7_h:g} h", inline=True)
    e.add_field(name="Last 24 h (created_on)", value=f"{last24_h:g} h", inline=True)
    return e


def embed_issue_list_intro(*, title: str, total: int, first_body: str) -> discord.Embed:
    """First chunk of a markdown issue list as embed description (truncated)."""
    desc = first_body.strip()
    if len(desc) > 3900:
        desc = desc[:3897] + "…"
    e = discord.Embed(
        title=title,
        description=desc or "_(empty)_",
        color=0x95A5A6,
    )
    e.set_footer(text=f"Showing up to {total} issue(s); more in follow-up messages.")
    return e
