"""workbench -- ticket-to-PR workflow tooling.

Design rule: policy lives here, in code, not in prompts. Skills choose a
subcommand from a closed set; they never compose a query, a URL or a field
list. Anything a model could invent is something this package decides.
"""

__version__ = "0.4.0"
