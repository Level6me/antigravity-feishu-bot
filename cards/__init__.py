"""Feishu interactive card builders (split modules)."""
from cards import common, models, indicators, response, projects, system, memory, stats_cards, auth, cron, plugin


class CardBuilder:
    """Facade preserving the historical CardBuilder.* API."""

    _create_footer = staticmethod(common.create_footer)

    build_model_panel = staticmethod(models.build_model_panel)
    build_model_switch_result_card = staticmethod(models.build_model_switch_result_card)

    _guess_intent = staticmethod(indicators._guess_intent)
    _get_dynamic_think_text = staticmethod(indicators._get_dynamic_think_text)
    build_typing_indicator = staticmethod(indicators.build_typing_indicator)
    build_tool_indicator = staticmethod(indicators.build_tool_indicator)
    build_download_indicator = staticmethod(indicators.build_download_indicator)
    build_streaming_indicator = staticmethod(indicators.build_streaming_indicator)
    clean_action_text = staticmethod(indicators.clean_action_text)
    build_stall_warning_card = staticmethod(indicators.build_stall_warning_card)
    build_stall_error_card = staticmethod(indicators.build_stall_error_card)

    build_ai_response = staticmethod(response.build_ai_response)

    build_dir_browser_card = staticmethod(projects.build_dir_browser_card)

    build_no_update_card = staticmethod(system.build_no_update_card)
    build_update_card = staticmethod(system.build_update_card)
    build_welcome_card = staticmethod(system.build_welcome_card)
    build_security_warning = staticmethod(system.build_security_warning)
    build_help_card = staticmethod(system.build_help_card)
    build_status_card = staticmethod(system.build_status_card)

    build_memory_card = staticmethod(memory.build_memory_card)
    build_note_list_card = staticmethod(memory.build_note_list_card)
    build_global_memory_card = staticmethod(memory.build_global_memory_card)

    build_quota_card = staticmethod(stats_cards.build_quota_card)
    build_context_card = staticmethod(stats_cards.build_context_card)

    build_auth_hint_card = staticmethod(auth.build_auth_hint_card)
    build_admin_welcome_card = staticmethod(auth.build_admin_welcome_card)
    build_auth_request_card = staticmethod(auth.build_auth_request_card)
    build_auth_result_card = staticmethod(auth.build_auth_result_card)
    build_user_panel_card = staticmethod(auth.build_user_panel_card)
    build_rate_limit_card = staticmethod(auth.build_rate_limit_card)

    @staticmethod
    def build_cron_panel_card(*args, **kwargs):
        import cards.cron as _cron
        return _cron.build_cron_panel_card(*args, **kwargs)

    @staticmethod
    def build_cron_start_card(*args, **kwargs):
        import cards.cron as _cron
        return _cron.build_cron_start_card(*args, **kwargs)

    @staticmethod
    def build_cron_execution_card(*args, **kwargs):
        import cards.cron as _cron
        return _cron.build_cron_execution_card(*args, **kwargs)

    @staticmethod
    def build_cron_created_card(*args, **kwargs):
        import cards.cron as _cron
        return _cron.build_cron_created_card(*args, **kwargs)

    build_plugin_panel_card = staticmethod(plugin.build_plugin_panel_card)

