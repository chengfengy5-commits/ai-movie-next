# Browser API connection

`create_app()` keeps its existing FastAPI return value and does not enable
cross-origin access. A deployment that serves a browser client from another
origin must explicitly wrap the finished app at its ASGI entry point:

```python
from haoai_backend.app import create_app
from haoai_backend.browser_cors import configure_browser_cors

asgi_app = configure_browser_cors(
    create_app(...),
    allowed_origins=("https://studio.example.com",),
)
```

Pass the wrapped `asgi_app` to the ASGI server. An empty `allowed_origins`
sequence returns the original app unchanged. Each configured value must be a
serialized `http://` or `https://` origin, without credentials, a path, query,
or fragment. Origins are matched exactly after scheme/host case and default
port normalization. Hostnames must be ASCII and already use the browser's
serialized form. For an internationalized domain, configure its explicit
punycode origin, such as `https://xn--fa-hia.example.com`; this helper rejects
Unicode host input instead of applying a different IDNA mapping. Wildcards and
the opaque `null` origin are not supported.

The middleware allows `GET`, `POST`, `PUT`, `DELETE`, and `OPTIONS`, plus only
the `Authorization`, `Content-Type`, `Idempotency-Key`, `Cache-Control`, and
`Pragma` request headers. Safety-sensitive AI requests that require both a
bearer token and an idempotency key can therefore pass the browser preflight.
The API still enforces authentication and idempotency. The middleware does not
allow cookies or other browser credentials. The client should continue using
bearer authentication with `credentials: "omit"`; CORS does not replace API
authentication. The client's `cache: "no-store"`, redirect, and referrer
policies remain client decisions.

This helper is an explicit ASGI composition function. It does not add an
environment setting or change `create_app()`, so a deployment must use the
wrapped app for browser access to take effect.
