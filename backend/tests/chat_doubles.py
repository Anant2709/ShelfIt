"""A fake OpenAI client that streams, shared by the chat tests.

Streaming is the only implementation in the service, so the double streams too.
Tool call arguments are deliberately splittable across chunks: that is how the
real API delivers them, and reassembling them is the part most likely to break.
"""

from types import SimpleNamespace


def chunk(content=None, tool_calls=None, choices=True):
    """One streamed chunk, shaped like the OpenAI SDK's."""
    if not choices:
        # Some providers end with a chunk carrying only usage information.
        return SimpleNamespace(choices=[])
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


def tool_delta(index=0, call_id=None, name=None, arguments=None):
    function = SimpleNamespace(name=name, arguments=arguments)
    return SimpleNamespace(index=index, id=call_id, function=function)


def text_chunks(*words):
    return [chunk(content=word) for word in words]


def tool_call_chunks(name, arguments, call_id="call-1", split=False):
    """A single tool call, optionally delivered in two fragments."""
    if not split:
        return [
            chunk(
                tool_calls=[
                    tool_delta(call_id=call_id, name=name, arguments=arguments)
                ]
            )
        ]
    midpoint = len(arguments) // 2
    return [
        chunk(
            tool_calls=[
                tool_delta(call_id=call_id, name=name, arguments=arguments[:midpoint])
            ]
        ),
        chunk(tool_calls=[tool_delta(arguments=arguments[midpoint:])]),
    ]


class FakeCompletions:
    """Returns a scripted stream per call, recording the requests it received."""

    def __init__(self, responses, error=None):
        self.responses = list(responses)
        self.error = error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            if callable(self.error):
                # Called with the call number, so a test can fail a later attempt.
                self.error(len(self.calls))
            else:
                raise self.error
        if not self.responses:
            return iter(())
        return iter(self.responses.pop(0))


class FakeClient:
    def __init__(self, responses, error=None):
        self.chat = SimpleNamespace(completions=FakeCompletions(responses, error))

    @property
    def calls(self):
        return self.chat.completions.calls


def connection_error():
    import httpx
    from openai import APIConnectionError

    return APIConnectionError(request=httpx.Request("POST", "https://api.openai.com"))
