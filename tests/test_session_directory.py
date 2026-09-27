from collections import deque

from hermes_home.bridge.production import StandardSessionDirectory


class ScriptedSocket:
    def __init__(self, result) -> None:
        self.incoming = deque(
            [
                {
                    "jsonrpc": "2.0",
                    "method": "event",
                    "params": {"type": "gateway.ready", "payload": {"version": "t"}},
                },
                {"jsonrpc": "2.0", "id": "home-1", "result": result},
            ]
        )
        self.sent: list[dict[str, object]] = []
        self.closed = False

    def send_json(self, frame):
        self.sent.append(frame)

    def receive_json(self, timeout=None):
        del timeout
        if not self.incoming:
            raise TimeoutError("fixture exhausted")
        return self.incoming.popleft()

    def close(self):
        self.closed = True


class Factory:
    def __init__(self, socket) -> None:
        self.socket = socket

    def open(self, url):
        del url
        return self.socket


def _directory(socket) -> StandardSessionDirectory:
    return StandardSessionDirectory(
        gateway_url="wss://standard.example/api/ws",
        hermes_token="server-secret",
        socket_factory=Factory(socket),
    )


def test_list_asks_standard_for_the_profile_and_normalizes_rows() -> None:
    socket = ScriptedSocket(
        {
            "sessions": [
                {
                    "id": "stored-1",
                    "title": "Groceries",
                    "preview": "private preview text",
                    "started_at": 10,
                    "message_count": 3,
                    "source": "tui",
                },
                {"id": "", "title": "dropped"},
                "not-a-row",
                {"id": "stored-2", "title": None, "started_at": "x"},
            ]
        }
    )

    rows = _directory(socket).list_sessions("amanda", 20)

    request = next(frame for frame in socket.sent if frame.get("method"))
    assert request["method"] == "session.list"
    # Over-fetched so scheduled jobs cannot crowd real conversations out.
    assert request["params"] == {"profile": "amanda", "limit": 200}
    assert rows == [
        {"id": "stored-1", "title": "Groceries", "started_at": 10, "message_count": 3},
        {"id": "stored-2", "title": "", "started_at": 0, "message_count": 0},
    ]
    assert socket.closed


def test_list_skips_scheduled_job_sessions_and_honours_the_limit() -> None:
    socket = ScriptedSocket(
        {
            "sessions": [
                {"id": "cron_abc_20260927_090045", "title": "", "source": "cron"},
                {"id": "stored-1", "title": "Groceries", "source": "home"},
                {"id": "cron_def_20260927_080035", "title": "", "source": "unknown"},
                {"id": "stored-2", "title": "Trip", "source": "CRON"},
                {"id": "stored-3", "title": "Dinner", "source": "tui"},
                {"id": "stored-4", "title": "Later", "source": "cli"},
            ]
        }
    )

    rows = _directory(socket).list_sessions("amanda", 2)

    assert [row["id"] for row in rows] == ["stored-1", "stored-3"]


def test_most_recent_is_the_newest_conversation_not_a_scheduled_job() -> None:
    socket = ScriptedSocket(
        {
            "sessions": [
                {"id": "cron_abc_20260927_090045", "title": "", "source": "cron"},
                {"id": "stored-9", "title": "Weekend trip", "source": "voice_session"},
            ]
        }
    )

    assert _directory(socket).most_recent("amanda") == "stored-9"
    request = next(frame for frame in socket.sent if frame.get("method"))
    assert request["method"] == "session.list"


def test_most_recent_is_none_when_only_scheduled_jobs_exist() -> None:
    socket = ScriptedSocket(
        {
            "sessions": [
                {"id": "cron_abc_20260927_090045", "title": "", "source": "cron"}
            ]
        }
    )

    assert _directory(socket).most_recent("amanda") is None
    assert _directory(ScriptedSocket({"sessions": []})).most_recent("amanda") is None
