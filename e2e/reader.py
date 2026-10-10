"""The e2e's reader: a `reporting` plugin on the SDK, standing in for the
reading roles, so the lake has someone whose reads it asks this plugin for.
It holds `custody` beside `reporting` only so that it can name the
instruments it asks about as a custodian's book would, by resolving their
identifiers (W3.1): bitcoin by its CAIP-19 and the custodian's symbol, never
resolved by Kraken (whose code for it is XXBT), a record the plugin names by
reading it (W3.6, a dgm's from contract v18 as ruled 2026-10-10); and the
dollar and the euro by ISO 4217.

It is not the plugin, and is never built into its image: `make e2e` runs it
from this file in a container of the plugin's image, which carries the SDK,
beside the plugin on core's harness. It answers its page as JSON for the
harness's runner (`page --instance reader --level read PATH`):

  /resolve                                    the records' IDs, by name;
  /follow?subject=ID...                       hears prices recorded for them;
  /heard                                      what it heard: each row's dataset,
                                              row key, version, kind and amount;
  /prices?subject=ID[&date=D][&kind=K][&dataset=D]
                                              one read: its rows, and what it
                                              left unanswered and why.
"""

import asyncio
import http.server
import json
import threading
import urllib.parse

import meridian
from meridian import Interface, Page, SourceChoice
from meridian.operations import as_money
from meridian.plugin.v1 import operations_pb2 as ops

PORT = 8088
LOOP = asyncio.new_event_loop()
ASKED = {
    "btc": [ops.Identifier(scheme="caip19", value="bip122:000000000019d6689c085ae165831e93/slip44:0"),
            ops.Identifier(scheme="symbol", value="BTC", source="e2e-custodian")],
    "usd": [ops.Identifier(scheme="iso4217", value="USD")],
    "eur": [ops.Identifier(scheme="iso4217", value="EUR")],
}


def price_json(price):
    meta, money = price.meta, as_money(price.price)
    return {
        "dataset": meta.source.dataset,
        "venue_id": meta.source.venue_id,
        "row_key": meta.row_key,
        "version": meta.version,
        "business_date": meta.business_date,
        "kind": ops.PriceKind.Name(price.kind),
        "amount": str(money.amount),
        "instrument_id": money.instrument_id,
        "currency_code": money.currency_code,
        "unconverted": [u.scheme for u in meta.unconverted],
        "raw": meta.raw.key,
    }


class Reader:
    def __init__(self, plugin):
        self.plugin = plugin
        self.heard = []
        self.following = None
        self.ids = {}

    async def resolve(self):
        for name, identifiers in ASKED.items():
            if name not in self.ids:
                found = await self.plugin.resolve_identifier(identifiers=identifiers)
                if not found.instrument_id:
                    raise RuntimeError(f"{name} did not resolve: {found}")
                self.ids[name] = found.instrument_id
        return dict(self.ids)

    async def on_price(self, heard):
        self.heard.append(price_json(heard.message.price))

    async def answer(self, path, query):
        one = lambda name: query.get(name, [""])[0]  # noqa: E731
        if path == "/resolve":
            return await self.resolve()
        if path == "/follow":
            if self.following is None:
                self.following = asyncio.ensure_future(
                    self.plugin.receive(prices_recorded=self.on_price, subjects=query["subject"]))
            return {"following": query["subject"]}
        if path == "/heard":
            return {"heard": self.heard}
        if path == "/prices":
            asked = {"subjects": [ops.SubjectRef(entity_id=s) for s in query["subject"]]}
            if one("dataset"):
                asked["sources"] = SourceChoice(named=[one("dataset")])
            if one("kind"):
                asked["kinds"] = [one("kind")]
            if one("date"):
                asked["business_date"] = one("date")
            answer = await self.plugin.list_prices(**asked)
            return {
                "prices": [price_json(p) for p in answer.prices],
                "unanswered": [{"subject": u.subject.entity_id, "dataset": u.dataset,
                                "reason": ops.UnansweredReason.Name(u.reason)}
                               for u in answer.unanswered],
            }
        return {"page": "the e2e's reader"}


def serve(reader):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            asked = reader.answer(parsed.path, urllib.parse.parse_qs(parsed.query))
            future = asyncio.run_coroutine_threadsafe(asked, LOOP)
            try:
                body, status = json.dumps(future.result(timeout=60), sort_keys=True), 200
            except Exception as failed:  # said on the page, for the run to quote
                body, status = json.dumps({"failed": f"{type(failed).__name__}: {failed}"}), 500
            data = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_args):
            pass

    http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


async def main():
    plugin = await meridian.connect(
        interface=Interface(PORT, "The e2e's reader", pages=[Page("/", "Prices", levels=["read"], roles=["reporting"])]),
    )
    reader = Reader(plugin)
    threading.Thread(target=serve, args=(reader,), daemon=True).start()
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.set_event_loop(LOOP)
    LOOP.run_until_complete(main())
