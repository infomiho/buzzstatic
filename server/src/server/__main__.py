from server.environment import InvalidEnvironment

try:
    from server.main import main

    main()
except InvalidEnvironment as exc:
    raise SystemExit(f"ERROR: {exc}") from None
