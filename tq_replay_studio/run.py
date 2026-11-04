import sys

if __name__ == "__main__":
    if "--market-worker" in sys.argv:
        from tq_workbench.sdk_worker import main
    elif "--history-worker" in sys.argv:
        from tq_workbench.history_worker import main
    elif "--trade-worker" in sys.argv:
        from tq_workbench.trade_worker import main
    else:
        from tq_workbench.app import main
    raise SystemExit(main())
