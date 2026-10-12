"""SSE responses with bounded sends and deterministic iterator cleanup."""

import anyio
from starlette.responses import StreamingResponse


class EventStreamResponse(StreamingResponse):
    def __init__(self, content, *, send_timeout: float = 15):
        super().__init__(
            content, media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
        self.send_timeout = send_timeout

    async def stream_response(self, send):
        async def bounded_send(message):
            with anyio.fail_after(self.send_timeout):
                await send(message)

        try:
            await super().stream_response(bounded_send)
        finally:
            # A suspended async generator is not automatically closed by async-for.
            with anyio.CancelScope(shield=True):
                await self.body_iterator.aclose()
