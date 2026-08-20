# Attribution

The contents of this directory are **derived from a third-party project**, not
original work in this repository.

**Upstream:** [twilio-samples/speech-assistant-openai-realtime-api-python](https://github.com/twilio-samples/speech-assistant-openai-realtime-api-python)
**Upstream license:** MIT, Copyright (c) 2024 pkamp (see `LICENSE`)

## What was changed

`main.py` was modified to add Form D tool calling. The upstream sample is a
plain speech-to-speech bridge with no tools. Changes:

- Imports `formd.tools` and registers all four tool schemas on `session.update`
- Added `execute_tool_call()`, which handles
  `response.function_call_arguments.done`, runs the requested tool, and returns
  the result as a `function_call_output` conversation item followed by
  `response.create`
- Replaced the generic assistant persona with a private-markets analyst prompt
  constrained to call a tool before stating any number
- Replaced the greeting TwiML
- Added `response.function_call_arguments.done` to the logged event types

Everything else, including the Twilio Media Streams bridging, the μ-law audio
handling, and the interruption/barge-in timing logic, is upstream code.

Files other than `main.py` are unmodified from upstream.
