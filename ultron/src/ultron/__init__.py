"""Ultron, Ecom Manager: an AI e-commerce manager for marketplace sellers. It watches the seller's
report bots, remembers what the owner teaches it, and answers the owner on Telegram. The code is
the same for every seller; each seller's own setup is agent.toml (names, accounts, actions, tools)
plus their secrets.

  core/          config (agent.toml), llm (model + tool loop), memory, approvals, runtime
  agents/        watch (checks bot runs), chat (answers the owner, proposes actions)
  host/          polling: ultron serve, on the seller's own machine
  tools/         built-in tool sets: runs, omsguru, memory
  portals/       portal clients shared by every seller
  integrations/  GitHub, Telegram, artifact decryption
"""
__version__ = "0.1.0"
NAME = "Ultron"
TAGLINE = "Ecom Manager"
TITLE = f"{NAME} · {TAGLINE}"
