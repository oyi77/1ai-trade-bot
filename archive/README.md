# Archive — Legacy Code

This directory holds historical code that has been migrated into the
``tradebot`` package.  **Do not import anything from these paths.**
All functionality is available from the ``tradebot`` namespace.

## Archived directories

| Directory         | Legacy location     | Replacement                    |
|-------------------|---------------------|--------------------------------|
| ``old-scripts/``  | ``scripts/``        | ``tradebot/`` package          |
| ``old-bots/``     | ``bots/``           | ``tradebot.bots``              |
| ``old-signals/``  | ``signals/``        | ``tradebot.signals``           |
| ``old-core/``     | ``core/``           | ``tradebot.{models, engines}`` |
| ``old-members/``  | ``members/``        | ``tradebot.services``          |
| ``old-strategies/``| ``strategies/``    | ``tradebot.engines``           |
| ``old-engines/``  | ``engines/``        | ``tradebot.engines``           |
| ``old-brokers/``  | ``brokers/``        | ``tradebot.brokers``           |

## Exception

``scripts/deriv/`` is **preserved in place** for backward compatibility.
Its imports are re-exported via ``tradebot.__init__`` with deprecation
warnings.
