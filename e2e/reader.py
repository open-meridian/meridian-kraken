"""The e2e's reader: a `reporting` plugin on the SDK, standing in for the
reading roles, so the lake has someone whose reads it asks this plugin for.

It is not the plugin, and is never built into its image: `make e2e` runs it
from this file in a container of the plugin's image, which carries the SDK,
beside the plugin on core's harness. It answers its page as JSON for the
harness's runner (`page --instance reader --level read PATH`):

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

    async def on_price(self, heard):
        self.heard.append(price_json(heard.message.price))

    async def answer(self, path, query):
        one = lambda name: query.get(name, [""])[0]  # noqa: E731
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
        interface=Interface(PORT, "The e2e's reader", pages=[Page("/", "Prices", levels=["read"])]),
    )
    reader = Reader(plugin)
    threading.Thread(target=serve, args=(reader,), daemon=True).start()
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.set_event_loop(LOOP)
    LOOP.run_until_complete(main())
