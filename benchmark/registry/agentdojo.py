"""Trusted AgentDojo suite registries and boundary facts."""

from pathlib import Path

from benchmark.registry.schema import load_environment

DEFAULT_PATH = Path(__file__).with_name("data") / "agentdojo" / "manifest.json"


def load(suite: str, path=DEFAULT_PATH):
    """Load the final, fully materialized registry for one suite."""
    return load_environment(path, suite, benchmark="AgentDojo")

COMMIT_ENTRIES = {
    "slack": frozenset({
        "add_user_to_channel", "send_direct_message", "send_channel_message",
        "invite_user_to_slack", "remove_user_from_slack", "post_webpage",
        "get_webpage",
    }),
    "workspace": frozenset({
        "send_email", "delete_email", "create_calendar_event",
        "cancel_calendar_event", "reschedule_calendar_event",
        "add_calendar_event_participants", "append_to_file", "create_file",
        "delete_file", "share_file",
    }),
    "banking": frozenset({
        "send_money", "schedule_transaction", "update_scheduled_transaction",
        "update_password", "update_user_info",
    }),
    "travel": frozenset({
        "create_calendar_event", "cancel_calendar_event", "reserve_hotel",
        "reserve_car_rental", "reserve_restaurant", "send_email",
    }),
    "shopping": frozenset({
        "send_money", "login_shopping_account", "verify_shopping_account",
        "update_shopping_account_password", "update_shopping_account_user_info",
        "get_shopping_linked_unread_emails", "refund_order", "cart_add_product",
        "cart_remove_product", "clear_cart", "checkout_selected_cart", "send_email",
        "download_file_through_url", "download_file_through_id", "create_file",
        "create_dir", "delete_node", "copy_node", "move_node", "input_to_webpage",
        "browse_webpage", "add_calendar_event_participants", "cancel_calendar_event",
        "create_calendar_event", "reschedule_calendar_event",
    }),
    "github": frozenset({
        "send_email", "download_file_through_url", "download_file_through_id",
        "create_file", "create_dir", "delete_node", "copy_node", "move_node",
        "input_to_webpage", "browse_webpage", "git_create_repo", "git_delete_repo",
        "git_transfer_repo_ownership", "git_clone", "git_invite_collaborators",
        "git_push", "git_pull", "git_issue", "git_star", "git_unstar",
        "get_github_linked_unread_emails", "git_add_ssh_key", "git_delete_ssh_key",
        "login_github_account", "update_github_account_password",
        "verify_github_account",
    }),
    "dailylife": frozenset({
        "send_money", "verify_transaction", "send_email", "get_unread_emails",
        "delete_email", "browse_webpage", "download_file_through_url",
        "download_file_through_id", "create_file", "create_dir", "delete_node",
        "copy_node", "move_node", "input_to_webpage",
        "add_calendar_event_participants", "cancel_calendar_event",
        "create_calendar_event", "reschedule_calendar_event",
    }),
}

MEDIATED_OBSERVATIONS = {
    "slack": frozenset({"get_webpage"}),
    "shopping": frozenset({"get_shopping_linked_unread_emails", "browse_webpage",
                            "input_to_webpage"}),
    "github": frozenset({"get_github_linked_unread_emails", "browse_webpage",
                          "input_to_webpage"}),
    "dailylife": frozenset({"get_unread_emails", "browse_webpage",
                             "input_to_webpage"}),
}

NATURAL_LANGUAGE_ARGUMENTS = {
    "slack": {
        "send_direct_message": ("body",),
        "send_channel_message": ("body",),
        "post_webpage": ("content",),
    },
    "banking": {
        "send_money": ("subject",),
        "schedule_transaction": ("subject",),
        "update_scheduled_transaction": ("subject",),
    },
    "travel": {
        "create_calendar_event": ("title", "description"),
        "send_email": ("subject", "body"),
    },
    "workspace": {
        "send_email": ("subject", "body"),
        "create_calendar_event": ("title", "description"),
        "append_to_file": ("content",),
        "create_file": ("content",),
    },
    "shopping": {
        "send_email": ("subject", "body"),
        "create_file": ("content",),
        "create_calendar_event": ("title", "description"),
    },
    "github": {
        "send_email": ("subject", "body"),
        "create_file": ("content",),
        "git_issue": ("title", "comment_body"),
    },
    "dailylife": {
        "send_money": ("subject",),
        "send_email": ("subject", "body"),
        "create_file": ("content",),
        "create_calendar_event": ("title", "description"),
    },
}

# Operator-side return roles. These describe protocol identity, not trusted
# content: each get_channels list element is the exact opaque handle accepted
# by downstream Slack calls, even when its display string carries untrusted
# material.
OUTPUT_TYPES = {
    "slack": {
        "get_channels": {"/*": "identity"},
    },
}
