import argparse

from marketgpr.commands.collect import register_args as register_collect, run as run_collect
from marketgpr.commands.clean import register_args as register_clean, run as run_clean
from marketgpr.commands.enrich import register_args as register_enrich, run as run_enrich
from marketgpr.commands.info import register_args as register_info, run as run_info


def main():
    parser = argparse.ArgumentParser(
        prog="marketgpr",
        description="MarketGPR — Kalshi prediction-market contract tools for geopolitical risk research.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    register_collect(sub.add_parser("collect", help="Build contract catalog from Kalshi API"))
    register_clean(sub.add_parser("clean", help="Filter a contract catalog by keywords and regex"))
    register_enrich(sub.add_parser("enrich", help="Enrich contract names with event titles from the Kalshi API"))
    register_info(sub.add_parser("info", help="Inspect a contract database — schema, rows, enrichment status"))

    args = parser.parse_args()

    if args.command == "collect":
        run_collect(args)
    elif args.command == "clean":
        run_clean(args)
    elif args.command == "enrich":
        run_enrich(args)
    elif args.command == "info":
        run_info(args)
