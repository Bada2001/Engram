"""
SupportTriage ↔ Engram integration — shows how to wire Engram into a ticket
classification pipeline.

How to wire up:
  1. Copy examples/support-triage/engram.yaml to your project root.
  2. pip install engram  (or pip install -e /path/to/Engram)
  3. Replace your existing classifier initialisation with the snippet below.
  4. After each classification call eng.observe().
  5. When a ticket is closed, call eng.outcome() with the resolved priority.
  6. Inject eng.active_lessons() into your classifier system prompt.
"""
from engram import Engram

# Singleton — import this wherever you need it
eng = Engram(schema="engram.yaml", db_path="engram.db")
eng.start()   # background scheduler starts here


# ---------------------------------------------------------------------------
# In your classifier module — after the model returns a priority label
# ---------------------------------------------------------------------------

def record_classification(
    ticket_id: str,
    predicted_priority: str,
    product_area: str,
    customer_tier: str,
    confidence: float,
):
    """Call this after every ticket classification."""
    eng.observe(
        decision_id = ticket_id,
        decision    = predicted_priority,
        context     = {
            "product_area":   product_area,
            "customer_tier":  customer_tier,
            "confidence":     confidence,
        },
    )


# ---------------------------------------------------------------------------
# In your ticket-close webhook — once the ticket is resolved
# ---------------------------------------------------------------------------

def record_resolution(ticket_id: str, actual_priority: str):
    """Call this when a ticket is closed and the real urgency is known."""
    eng.outcome(ticket_id, label=actual_priority)


# ---------------------------------------------------------------------------
# In your classifier system prompt — inject active lessons
# ---------------------------------------------------------------------------

def get_lessons_block() -> str:
    """Returns a formatted string to insert into the classifier system prompt."""
    lessons = eng.active_lessons()
    if not lessons:
        return ""
    lines = "\n".join(f"- {l}" for l in lessons)
    return f"\n\n## Lessons from recent misclassifications\n{lines}\n"
