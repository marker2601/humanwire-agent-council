"""Repository wrapper for the installed HumanWire smoke proof."""

from humanwire.smoke import OfflineProof, main, run_offline_proof

__all__ = ["OfflineProof", "main", "run_offline_proof"]


if __name__ == "__main__":
    raise SystemExit(main())
