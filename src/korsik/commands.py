"""Slash commands derive from the shared public action registry."""

from .menu import ACTIONS

PUBLIC_COMMANDS = [{"name": a.id, "description": a.description} for a in ACTIONS]


def configured_commands(settings):
    return [
        {"name": a.id, "description": a.description}
        for a in ACTIONS
        if not a.optional or settings.enable_optional
    ]
