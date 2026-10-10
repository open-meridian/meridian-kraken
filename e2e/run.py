"""`make e2e` and `make e2e-recorded`: the plugin end to end on core's plugin
harness (contract v18), in synthetic mode, beside a reader (e2e/reader.py).

1. Launched: the plugin registers its catalogue; the deployment admin's tool
   lists its two datasets, `daily` and `live`, each on the UTC day ending at
   00:00, with their modes.
2. Synthetic mode on: the plugin maps its pairs, each asset resolved to a
   record the deployment minted -- bitcoin by its CAIP-19, USDC by its code
   at the venue, the dollar and the euro by ISO 4217 -- listed on the Instruments page; the
   venue, which the harness's deployment does not hold (it has no
   platform), reported missing.
3. Licensed and entitled: the admin licenses both datasets and entitles the
   reader to them, the broker reloaded with each.
4. A close wanted: the reader reads BTC's closes for a day nothing has
   recorded; the lake asks the plugin, which records XXBTZUSD's close in
   dollars and XBTUSDC's on USDC's own instrument, exactly as the venue
   stated them, on that business date, each naming its raw record and the
   venue as reported; the reader reads both, and hears them.
5. Declined, and a rate: a read of the dollar's closes, which no pair's base
   is, is declined as not covered; the euro's, recorded from EUR/USD in
   dollars: an FX rate on a currency's cash instrument.
6. Live: a read of BTC's latest on the live dataset stands as a want, which
   the plugin keeps current -- the last trade's price and the best bid and
   offer of each product -- until the lake withdraws it.
7. Parity: the plugin's two read tools answer through the deployment's MCP
   what its two pages show.
8. Its raw responses are in its granted storage, and `store lake` prints
   each row kept, numbered with no holes, the wants asked, answered,
   declined and withdrawn, and the venue's miss.

Run from the repository's root by the Makefile, which builds the image,
copies the harness out and writes the plugins' compose file first, and
exports what the compose files read. Standard library only.
"""

import json
import re
import subprocess
import sys
import time

VENUE = "kraken"
NAME = "Kraken"
#: `make e2e-recorded` names the day its recorded exchanges hold
#: (`--day=YYYY-MM-DD`): the run then reads that day, and holds the rows to
#: their shape rather than to the synthetic exchange's values.
DAY = [a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--day=")]
EXACT = not DAY
CLOSED = DAY[0] if DAY else "2026-10-08"
LOG = ".e2e/runner.log"
#: A product quoted in dollars, and one quoted in USDC.
FIAT_PRODUCT, STABLE_PRODUCT = "XXBTZUSD", "XBTUSDC"
#: The harness's compose files and this plugin's, and any the Makefile adds
#: (`make e2e-recorded`'s e2e/recorded.yaml), given as arguments.
COMPOSE = ["docker", "compose", "-p", f"{VENUE}-e2e", "-f", ".e2e/harness/compose.yaml",
           "-f", ".e2e/harness/plugins.yaml", "-f", "e2e/plugin.yaml",
           *[a for a in sys.argv[1:] if not a.startswith("--day=")]]
#: What the synthetic exchange states, which the rows must carry exactly.
CLOSES = {"XXBTZUSD": "62001.40000", "XBTUSDC": "62006.75"}
LAST = {"XXBTZUSD": "61988.90000", "XBTUSDC": "62006.75"}
#: The euro's close in dollars that day (ZEURZUSD): an FX rate.
EUR_CLOSE = "1.0925"


class Failed(Exception):
    pass


def log(text):
    with open(LOG, "a", encoding="utf-8") as kept:
        kept.write(text + "\n")


def compose(*args, check=True):
    done = subprocess.run(COMPOSE + list(args), capture_output=True, text=True, timeout=600)
    log(f"$ compose {' '.join(args)[:300]}\n{done.stdout[-4000:]}{done.stderr[-2000:]}")
    if check and done.returncode != 0:
        raise Failed(f"compose {' '.join(args)[:200]}: {(done.stdout + done.stderr).strip()[-600:]}")
    return done


def runner(*args, check=True):
    done = compose("run", "--rm", "-T", "runner", *args, check=check)
    return done.stdout if check else done


def must(condition, why):
    if not condition:
        raise Failed(why)


def until(seconds, attempt, what):
    deadline = time.monotonic() + seconds
    last = None
    while True:
        try:
            answer = attempt()
        except Failed as failed:
            answer, last = None, failed
        if answer:
            return answer
        if time.monotonic() > deadline:
            raise Failed(f"{what}, in {seconds} seconds" + (f": {last}" if last else ""))
        time.sleep(2)


def page(instance, level, path):
    said = runner("page", "--instance", instance, "--level", level, path)
    status, _, body = said.partition("\n")
    must(status.strip() == "200", f"{instance} {path} answered {said[-600:]}")
    return body


def read(path):
    return json.loads(page("reader", "read", path))


def call(tool, arguments, *outcomes):
    done = runner("mcp", "call", tool, json.dumps(arguments), check=False)
    try:
        answer = json.loads(done.stdout)
    except ValueError:
        raise Failed(f"{tool}: no answer: {(done.stdout + done.stderr)[-400:]}") from None
    must(answer.get("outcome") in outcomes,
         f"{tool} answered {answer.get('outcome')}, not {outcomes}: {done.stdout[-700:]}")
    return answer


def instrument(listed, pattern):
    for line in listed.splitlines():
        instrument_id, _, identifiers = line.partition("\t")
        if re.search(pattern, identifiers):
            return instrument_id
    raise Failed(f"no record listed matches {pattern}: {listed}")


def run():
    started = time.time()
    compose("down", "-v", "--remove-orphans", check=False)
    compose("up", "-d")
    for instance in (VENUE, "reader"):
        runner("ready", "--instance", instance)
    runner("grant", "--instance", "reader", "--level", "read")
    runner("grant", "--instance", VENUE, "--level", "admin")
    runner("mcp", "connect", "--covers", "deployment_admin", "--covers", f"{VENUE}:admin")
    tools = runner("mcp", "list").split()
    named = {t.rsplit("__", 1)[-1]: t for t in tools}
    for name in ("dashboard__list_datasets", "read_connection", "read_datasets"):
        must(name.rsplit("__", 1)[-1] in named, f"step 1, {name} is listed: {tools}")

    # 1. The catalogue, as the deployment admin's tool lists it.
    def datasets():
        held = {d["dataset"]: d for d in
                call("dashboard__list_datasets", {}, "read")["data"]["datasets"]}
        return held if {f"{VENUE}:daily", f"{VENUE}:live"} <= set(held) else None
    held = until(90, datasets, "step 1, the two datasets were not listed")
    for key, modes in (("daily", ["pull", "push"]), ("live", ["pull", "stream"])):
        entry = held[f"{VENUE}:{key}"]["catalogue_entry"]
        must(entry["modes"] == modes and entry["day_time_zone"] == "Etc/UTC"
             and entry.get("day_end_minute", 0) == 0, f"step 1, {key}'s entry: {entry}")

    # 2. Synthetic mode: the products mapped, each asset resolved.
    runner("settings", "--instance", VENUE, "synthetic=true")
    listed = until(90, lambda: (lambda o: o if "slip44:0" in o and "USDC" in o else None)(
        runner("instruments")), "step 2, the product map's records were not listed")
    log("instruments:\n" + listed)
    btc = instrument(listed, r"bip122:000000000019d6689c085ae165831e93/slip44:0")
    usdc = instrument(listed, r"(?<![A-Z])USDC(?![A-Z])")
    usd = instrument(listed, r"iso4217\W+USD(?![A-Z])|(?<![A-Z])USD(?![A-Z]).*iso4217")
    eur = instrument(listed, r"iso4217\W+EUR(?![A-Z])|(?<![A-Z])EUR(?![A-Z]).*iso4217")

    # 3. Licensed and entitled.
    runner("licence", f"{VENUE}:daily", "kept=true", "retention_days=0", "derived_use=true",
           "display=true", "personal_use=false", f"note={NAME}'s market data terms.")
    runner("licence", f"{VENUE}:live", "kept=true", "retention_days=2", "display=true",
           "personal_use=false", "note=Kept two days.")
    for key in ("daily", "live"):
        runner("entitle", "--instance", "reader", f"{VENUE}:{key}", "note=The e2e's reader.")

    # 4. A close wanted, recorded against the want, read and heard.
    daily = f"/prices?subject={btc}&date={CLOSED}&kind=close&dataset={VENUE}:daily"
    first = read(daily)
    # The lake asks the plugin at once: unanswered as asked, or, where the
    # plugin's answer is already in, as the source being silent so far.
    must(not first["prices"] and {u["reason"] for u in first["unanswered"]}
         <= {"UNANSWERED_REASON_ASKED_SOURCE", "UNANSWERED_REASON_SOURCE_SILENT"}
         and first["unanswered"], f"step 4, a day not recorded is asked of the plugin: {first}")
    closes = until(90, lambda: (lambda r: r if len(r) >= (2 if EXACT else 1) else None)(
        read(daily)["prices"]),
                   "step 4, the plugin did not record the closes wanted")
    by_product = {p["row_key"].split(":")[0]: p for p in closes}
    for product, amount in CLOSES.items():
        row = by_product[product] if EXACT else by_product.get(product)
        if row is None:
            continue
        must((row["amount"] == amount or not EXACT) and row["business_date"] == CLOSED,
             f"step 4, {product}'s close as the venue stated it: {row}")
        must(row["venue_id"] == "" and row["unconverted"] == [f"{VENUE}:venue"],
             f"step 4, the venue not held, named as reported: {row}")
        must(row["raw"].startswith("responses/"), f"step 4, its raw record: {row}")
    dollars = by_product.get(FIAT_PRODUCT) or next(iter(by_product.values()))
    must(dollars["instrument_id"] not in ("", usdc, btc),
         f"step 4, a dollar price names the dollar's cash instrument: {dollars}")
    stable = by_product.get(STABLE_PRODUCT, {"instrument_id": usdc, "currency_code": ""})
    must(stable["instrument_id"] == usdc and stable["currency_code"] == "",
         f"step 4, a USDC price on USDC's own instrument, never a currency: {stable}")
    # Followed: the reader's receive reads what the lake holds for BTC, and
    # its latest on each dataset is asked of the plugin -- the daily
    # dataset's being the past week's days.
    read(f"/follow?subject={btc}")
    until(60, lambda: [h for h in read("/heard")["heard"] if h["dataset"] == f"{VENUE}:daily"],
          "step 4, the reader did not hear the closes")

    # 5. Declined: no product's base is the dollar. And an FX rate: the euro's
    #    cash instrument priced in dollars, as Kraken's EUR/USD states it.
    dollar = f"/prices?subject={usd}&date={CLOSED}&kind=close&dataset={VENUE}:daily"
    read(dollar)
    euro = f"/prices?subject={eur}&date={CLOSED}&kind=close&dataset={VENUE}:daily"
    read(euro)
    rate = until(90, lambda: read(euro)["prices"], "step 5, the plugin did not record the rate")
    must(len(rate) == 1 and (rate[0]["amount"] == EUR_CLOSE or not EXACT) and rate[0]["row_key"].startswith("ZEURZUSD:")
         and rate[0]["currency_code"] in ("USD", ""), f"step 5, the euro in dollars: {rate}")

    # 6. Live: kept current while it stands, then withdrawn.
    live = f"/prices?subject={btc}&dataset={VENUE}:live"
    read(live)
    # The lake answers the latest per subject, dataset, kind and venue, so a
    # read gives one product's last price; `store lake` holds every one.
    def live_rows():
        rows = read(live)["prices"] + [h for h in read("/heard")["heard"]
                                       if h["dataset"] == f"{VENUE}:live"]
        return [r for r in rows if r["kind"] == "PRICE_KIND_LAST"]
    lasts = until(90, live_rows, "step 6, the plugin did not record the live prices")
    for row in lasts:
        stated = LAST.get(row["row_key"].split(":")[0])
        must((row["amount"] == stated or not EXACT) and not row["business_date"],
             f"step 6, a last price as stated, keyed by its instant: {row}")

    # 7. Parity: each read tool answers what its page shows.
    connection = call(named["read_connection"], {}, "read", "unchanged")["data"]
    mode = "synthetic" if EXACT else "recorded"
    must(connection["mode"] == mode and connection["venue"] == NAME
         and connection["venue_held"] is False and connection["calls"] > 0,
         f"step 7, read_connection: {connection}")
    shown = page(VENUE, "admin", "/")
    must(f"{mode.capitalize()}: answers from files" in shown and NAME in shown,
         "step 7, the Connection page shows the same")
    served = call(named["read_datasets"], {}, "read", "unchanged")["data"]
    must([d["dataset"] for d in served["datasets"]] == [f"{VENUE}:daily", f"{VENUE}:live"]
         and served["products_mapped"] >= 5 and served["recorded"] >= 2,
         f"step 7, read_datasets: {served}")
    shown = page(VENUE, "admin", "/datasets")
    must(f"{served['products_mapped']} of {served['products_listed']} products mapped" in shown,
         "step 7, the Datasets page shows the same")

    # 8. Raw responses kept, and the lake's record.
    kept = compose("exec", "-T", VENUE, "python", "-c",
                   "from meridian import edge; "
                   "print(sum(1 for p in (edge.storage_dir() / 'responses').rglob('*') if p.is_file()))")
    must(int(kept.stdout.strip() or 0) >= 5, f"step 8, raw responses in storage: {kept.stdout}")
    names = {btc: "BTC", usdc: "USDC", usd: "USD", eur: "EUR"}

    def lake():
        lines = compose("run", "--rm", "-T", "store", "lake").stdout.split("\n")
        for instrument_id, name in names.items():
            lines = [line.replace(instrument_id, name) for line in lines]
        withdrawn = any(line.startswith(f"want|{VENUE}:live|withdrawn|") for line in lines)
        declined = any(line.startswith(f"want|{VENUE}:daily|declined|USD|") for line in lines)
        return lines if withdrawn and declined else None
    lines = until(240, lake, "step 8, the live want was not withdrawn, or the dollar not declined")
    log("store lake:\n" + "\n".join(lines))
    rows = [line.split("|") for line in lines if line.startswith(f"row|{VENUE}:daily|")]
    sequences = sorted(int(row[3]) for row in rows)
    must(sequences == list(range(1, len(sequences) + 1)) and len(sequences) >= 2,
         f"step 8, daily's rows numbered from 1 with no holes: {sequences}")
    must({row[6] for row in rows} == {"BTC", "EUR"} and CLOSED in {row[9] for row in rows},
         f"step 8, each row BTC's or the euro's, each on its business date: {rows}")
    must(all(row[8] == "" and row[11] == "true" for row in rows),
         f"step 8, the venue left empty, a value as reported: {rows}")
    must(f"want|{VENUE}:daily|asked|BTC|0" in lines, f"step 8, the want asked: {lines}")
    must(any(re.fullmatch(rf"want\|{VENUE}:daily\|answered\|BTC(,BTC)*\|0", line) for line in lines),
         f"step 8, the want answered, a subject for each row: {lines}")
    for product in LAST if EXACT else []:
        for kind in ("last", "bid", "ask"):
            must(any(line.startswith(f"row|{VENUE}:live|price|") and f"|{product}:{kind}:" in line
                     for line in lines), f"step 8, {product}'s {kind} kept: {lines}")
    must(any(line.startswith(f"miss|{VENUE}|") for line in lines),
         f"step 8, the venue's miss counted: {lines}")

    print(f"e2e OK in {int(time.time() - started)}s: on core's plugin harness, the catalogue's "
          "daily and live datasets listed by the deployment admin's tool on the UTC day; the "
          "pair map made in synthetic mode, bitcoin, USDC, the dollar and the euro resolved and the venue "
          "reported missing; both datasets licensed and the reader entitled; a day's closes "
          "wanted and recorded against the want, XXBTZUSD's in dollars and XBTUSDC's on USDC's "
          "own instrument, exactly, read and heard; the dollar declined as not covered and the "
          "euro's rate in dollars recorded; bitcoin's "
          "live prices kept current until withdrawn; read_connection and read_datasets "
          "answering what the pages show; raw responses in storage; and `store lake` "
          "numbering each row with no holes, the wants and the miss")


def main():
    open(LOG, "w").close()
    try:
        run()
    except (Failed, subprocess.TimeoutExpired, KeyError, ValueError, IndexError) as failed:
        print(f"e2e FAILED: {failed}; the run is in {LOG}, the components' logs in "
              ".e2e/components.log", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
