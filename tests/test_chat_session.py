"""Pure unit tests for core.chat_session.ChatSession (no Qt)."""

from __future__ import annotations

import unittest

from core.chat_session import ChatSession


class TestChatSession(unittest.TestCase):
    def test_token_append_and_finish(self) -> None:
        s = ChatSession()
        s.add_user("hello")
        s.begin_assistant()
        s.append_token("Hi")
        s.append_token(" there")
        msg = s.finish_assistant()
        self.assertIsNotNone(msg)
        self.assertEqual(msg["content"], "Hi there")
        self.assertEqual(s.status, "idle")
        self.assertEqual(s.messages[-1]["role"], "assistant")
        self.assertEqual(s.current_stream_text(), "")

    def test_stop_keeps_partial(self) -> None:
        s = ChatSession()
        s.begin_assistant()
        s.append_token("partial reply")
        msg = s.stop_assistant()
        self.assertIsNotNone(msg)
        self.assertTrue(msg["stopped"])
        self.assertEqual(msg["content"], "partial reply")
        self.assertEqual(s.status, "idle")
        self.assertEqual(len(s.messages), 1)

    def test_late_tokens_ignored(self) -> None:
        s = ChatSession()
        s.begin_assistant()
        s.append_token("ok")
        s.finish_assistant()
        s.append_token(" should not appear")
        self.assertEqual(s.messages[-1]["content"], "ok")
        self.assertEqual(s.current_stream_text(), "")

    def test_late_tokens_after_stop_ignored(self) -> None:
        s = ChatSession()
        s.begin_assistant()
        s.append_token("half")
        s.stop_assistant()
        s.append_token(" leaked")
        self.assertEqual(s.messages[-1]["content"], "half")
        self.assertTrue(s.messages[-1]["stopped"])

    def test_model_messages_after_compaction(self) -> None:
        s = ChatSession()
        for i in range(6):
            s.add_user(f"u{i}")
            s.begin_assistant()
            s.append_token(f"a{i}")
            s.finish_assistant()
        compacted = [
            {"role": "system", "content": "[Summary of earlier conversation:\nOlder turns]"},
            {"role": "user", "content": "u4"},
            {"role": "assistant", "content": "a4"},
            {"role": "user", "content": "u5"},
            {"role": "assistant", "content": "a5"},
        ]
        original_len = len(s.messages)
        s.apply_compaction(compacted)
        self.assertEqual(len(s.messages), original_len)
        self.assertEqual(s.summary_text, "Older turns")
        self.assertEqual(s.summary_upto, original_len - 4)
        mm = s.model_messages()
        self.assertEqual(mm[0]["role"], "system")
        self.assertIn("[Summary of earlier conversation:", mm[0]["content"])
        self.assertIn("Older turns", mm[0]["content"])
        self.assertEqual(len(mm), 5)
        self.assertEqual(mm[-1]["content"], "a5")

    def test_to_dict_from_dict_round_trip(self) -> None:
        s = ChatSession()
        s.add_user("ping", attachments=[{"id": "w1", "name": "demo"}])
        s.begin_assistant()
        s.append_token("pong")
        s.finish_assistant()
        s.summary_text = "earlier"
        s.summary_upto = 0
        s.workspace_id = "ws-1"
        data = s.to_dict()
        s2 = ChatSession.from_dict(data)
        self.assertEqual(s2.id, s.id)
        self.assertEqual(len(s2.messages), len(s.messages))
        self.assertEqual(s2.messages[0]["content"], "ping")
        self.assertEqual(s2.messages[0]["attachments"][0]["id"], "w1")
        self.assertEqual(s2.summary_text, "earlier")
        self.assertEqual(s2.workspace_id, "ws-1")
        self.assertEqual(s2.status, "idle")

    def test_error_storage(self) -> None:
        s = ChatSession()
        s.add_user("go")
        s.begin_assistant()
        s.append_token("nope")
        err = s.add_error("Chat request failed: timeout")
        self.assertTrue(err["error"])
        self.assertIn("timeout", err["content"])
        self.assertEqual(s.status, "error")
        self.assertEqual(s.messages[-1]["error"], True)

    def test_draft_recovered_as_stopped(self) -> None:
        s = ChatSession()
        s.add_user("write a poem")
        data = s.to_dict()
        data["draft_assistant"] = "Roses are"
        data["status"] = "generating"
        s2 = ChatSession.from_dict(data)
        self.assertTrue(s2.messages[-1].get("stopped"))
        self.assertEqual(s2.messages[-1]["content"], "Roses are")
        self.assertIsNone(s2.draft_assistant)

    def test_stopping_research_keeps_progress_draft(self) -> None:
        s = ChatSession()
        s.begin_assistant("researching")
        s.draft_assistant = "Research is on step 3"
        stopped = s.stop_assistant()
        self.assertEqual(stopped["content"], "Research is on step 3")
        self.assertTrue(stopped["stopped"])
        self.assertEqual(s.status, "idle")

    def test_finish_ignored_when_idle(self) -> None:
        s = ChatSession()
        s.begin_assistant()
        s.append_token("keep")
        s.stop_assistant()
        self.assertIsNone(s.finish_assistant("duplicate from worker"))
        self.assertEqual(len(s.messages), 1)


if __name__ == "__main__":
    unittest.main()
