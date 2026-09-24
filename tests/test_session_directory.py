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
    assert request["params"] == {"profile": "amanda", "limit": 20}
    assert rows == [
        {"id": "stored-1", "title": "Groceries", "started_at": 10, "message_count": 3},
        {"id": "stored-2", "title": "", "started_at": 0, "message_count": 0},
    ]
    assert socket.closed


def test_most_recent_returns_the_stored_id_or_none() -> None:
    assert (
        _directory(ScriptedSocket({"session_id": "stored-9"})).most_recent("amanda")
        == "stored-9"
    )
    assert (
        _directory(ScriptedSocket({"session_id": None})).most_recent("amanda") is None
    )
