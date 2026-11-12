# Folio Python client (preview)

An independently installable asynchronous client for the Folio API. Version
0.1.0 is a repository preview, not a published PyPI release or a production
identity integration.

```sh
pip install ./sdk/python
```

```python
import asyncio
import os
from folio_client import AsyncFolioClient

async def main():
    async with AsyncFolioClient("http://localhost:8000") as folio:
        await folio.login(os.environ["FOLIO_EMAIL"], os.environ["FOLIO_PASSWORD"])
        sources = await folio.retrieve("What was APAC revenue?")
        answer = await folio.ask("What was APAC revenue?")
        print(answer.answer)
        print(answer.security_trace)
        print(answer.evidence_checks)
        await folio.logout()

asyncio.run(main())
```

Use HTTPS outside localhost. A `token=` constructor argument accepts an existing
revocable **Folio session token**, not an Azure API key or arbitrary Entra token.
Use a separate client/session for each end user; an administrator session must
not represent multiple end users. The server decides tenant, roles and access.
The client does not cache results, infer permissions, retry requests automatically,
or follow redirects. Passwords/tokens are not written to disk by this package.

Methods return typed `User`, `Source` and `Answer` objects. `document_ids=` can
restrict retrieval/answers; forbidden scopes preserve server HTTP errors through
`httpx.HTTPStatusError`. `stream_answer()` returns the final typed answer, closes
the response stream, and raises when the server sends an error after HTTP 200.
`stream_events()` exposes status/answer/error events for applications that need
progress; callers must handle error events. Close the client with `async with`
or `await client.aclose()`.

`grounded` reflects the server's current validation contract, not proof of factual
entailment. Inspect `evidence_checks.scope/status` when the optional financial
literal guard is enabled; `not_checked` is not a successful claim-level check.
Source ACL synchronization, managed SSO and a stable production SDK contract
remain future work.
