from unittest.mock import MagicMock

from hebelbot.main import Bot


def make_bot(settings, client, store):
    bot = Bot.__new__(Bot)
    bot.settings = settings
    bot.telegram = MagicMock()
    bot.commands = MagicMock()
    bot.commands.handle.return_value = "antwort"
    return bot


def update(chat_id, text):
    return {"update_id": 1, "message": {"chat": {"id": chat_id}, "text": text}}


def test_only_configured_chat_may_send_commands(settings, client, store):
    bot = make_bot(settings, client, store)
    bot.handle_update(update(999, "/remove FC7CVG"))
    bot.commands.handle.assert_not_called()
    bot.telegram.send.assert_not_called()

    bot.handle_update(update(settings.telegram_chat_id, "/list"))
    bot.commands.handle.assert_called_once_with("/list")
    bot.telegram.send.assert_called_once_with("antwort")


def test_command_crash_is_reported_not_raised(settings, client, store):
    bot = make_bot(settings, client, store)
    bot.commands.handle.side_effect = RuntimeError("boom")
    bot.handle_update(update(settings.telegram_chat_id, "/list"))
    assert "Interner Fehler" in bot.telegram.send.call_args[0][0]
