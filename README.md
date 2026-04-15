# Engram

**A plug-in learning layer for AI decision systems.**

Engram wraps any system that makes decisions and helps it learn from its own outcomes over time. It records what your system decided, tracks whether those decisions were correct, extracts lessons from patterns, and feeds those lessons back into your prompts — closing the loop automatically.

---

## Who this is for

Engram is for engineers building AI systems that make repeated decisions in production — and need those systems to improve over time based on real outcomes.

If your system does any of the following, Engram is a fit:

- An **LLM answers questions** and users or reviewers rate the responses
- An **AI agent routes, approves, or rejects** requests and outcomes are eventually known
- A **classifier or moderation system** labels content and humans occasionally override it
- A **RAG pipeline** retrieves and generates answers and you track whether they were correct
- Any AI system where you can say, after the fact, *"that decision was right"* or *"that decision was wrong"*

**What you get:** your system's prompt and decision policy evolve automatically based on what's actually working in production — without retraining a model, without manual log analysis, without guessing.

**What Engram is not:** a model training framework, an evaluation harness for offline benchmarks, or a general observability tool. It is specifically a learning layer for AI systems that make decisions and receive feedback.

---

## How it works

```
Your AI system
     │
     ├─ eng.observe(id, decision, context)   ← record every decision
     ├─ eng.outcome(id, "correct"|"wrong")   ← feed back results
     │
     └─ eng.active_lessons()                 ← inject lessons into prompts
              ▲
              │
     ┌────────┴──────────────────────────────────┐
     │              Background scheduler          │
     │                                            │
     │  Daily diary    → summarises decisions     │
     │  Lesson extract → distils patterns         │
     │  Proposals      → suggests improvements   │
     └────────────────────────────────────────────┘
```

Every day Engram writes a diary of your system's decisions and outcomes. Weekly it extracts actionable lessons from the pattern. Nightly it generates concrete improvement proposals — prompt changes, threshold adjustments, architecture suggestions — backed by evidence from your own data. You review and apply them via the CLI.

---

## A real use case: customer support bot that improves itself

Most AI systems get deployed and stay frozen. You ship a prompt, it runs for months, and when it starts making mistakes you have no idea why — because you never built a feedback loop.

Here's the problem in concrete terms.

You built a customer support bot. It handles 500 queries a day. Users can rate responses with a thumbs up or down. You check in once a week and see accuracy has dropped from 87% to 71%. You have no idea what changed. Was it a new type of query? A bad prompt? A retrieval issue? You dig through logs for hours and find nothing actionable.

**This is exactly what Engram is for.**

---

### Step 1 — describe your system

```yaml
# engram.yaml
name: "SupportBot"
domain: |
  A customer support AI that answers product questions using a RAG pipeline
  over our documentation. It makes one of three decisions per query:
    - ANSWERED: confident enough to respond directly
    - ESCALATED: low confidence, handed to a human agent
    - REFUSED: out of scope, rejected with explanation

  Context fields available per decision:
    - confidence: model self-reported confidence (0.0–1.0)
    - retrieval_score: top chunk cosine similarity
    - query_category: billing | technical | account | other
    - response_length: number of tokens in the response

outcome:
  strategy: binary   # user thumbs up/down drives this

parameters:
  - name: confidence_threshold
    type: float
    description: "Below this, ESCALATE instead of ANSWER. Currently 0.75."

  - name: retrieval_score_threshold
    type: float
    description: "Minimum chunk similarity to use. Below this, we may hallucinate."

proposal_categories:
  - prompt
  - retrieval
  - routing
  - cost
```

The `domain` block is the most important thing you write. The richer it is, the more specific the proposals will be.

---

### Step 2 — instrument your bot

Two lines added to your existing handler. That's the entire integration.

```python
from engram import Engram

eng = Engram("engram.yaml")
eng.start()

def handle_query(query_id: str, query: str) -> str:
    chunks, retrieval_score = retrieve(query)
    response, confidence = generate(query, chunks)

    # Decide what to do
    if confidence < 0.75:
        decision = "ESCALATED"
    elif not chunks:
        decision = "REFUSED"
    else:
        decision = "ANSWERED"

    # Record the decision — one line
    eng.observe(query_id, decision, context={
        "confidence": confidence,
        "retrieval_score": retrieval_score,
        "query_category": classify(query),
        "response_length": len(response.split()),
    })

    return response
```

And wherever you collect user feedback:

```python
def on_user_feedback(query_id: str, thumbs_up: bool):
    eng.outcome(query_id, "correct" if thumbs_up else "wrong")
```

---

### Step 3 — inject lessons into the prompt

This is what closes the loop. Instead of a static system prompt, you build it dynamically:

```python
def build_system_prompt() -> str:
    base = """You are a customer support agent for Acme Corp.
Answer questions about our product using only the provided documentation.
If you are not confident, say so."""

    lessons = eng.active_lessons()
    if lessons:
        base += "\n\nLessons learned from past interactions:\n"
        base += "\n".join(f"- {l}" for l in lessons)

    return base
```

On day one, `active_lessons()` returns nothing. By week two it might return things like:

> - Billing queries about invoice dates are frequently wrong — prioritise chunks from the billing FAQ over general docs
> - Queries containing "cancel" are escalated 3x more than average but 80% end up correct — consider lowering the confidence threshold for this category
> - Responses over 300 tokens receive significantly lower ratings — keep answers concise

These are extracted automatically from your own system's history.

---

### Step 4 — review proposals in the morning

Every morning you run:

```bash
engram review
```

And you see things like:

```
[4] ○ [HIGH] [routing] Lower escalation threshold for billing queries

     Date: 2026-04-14  Status: pending
     Problem: Billing queries are escalated at 3x the rate of other categories
              but have the same accuracy when answered. Escalation is too aggressive.
     Evidence:
       · query-8821: billing/invoice → ESCALATED (confidence 0.76, threshold 0.75)
       · query-8934: billing/cancel  → ESCALATED (confidence 0.77, threshold 0.75)
     Proposal: Lower confidence_threshold for billing category from 0.75 to 0.65.
               Billing queries have well-structured docs and the model is calibrated
               well for this domain. The current threshold is not earning its cost.

[5] ○ [MEDIUM] [retrieval] Increase retrieval_score_threshold for "other" category

     Problem: 23% of "other" queries produce wrong answers, vs 8% for technical/billing.
              These queries pull low-scoring chunks (avg 0.41) and the model hallucinates.
     Proposal: For queries classified as "other", raise the retrieval threshold to 0.55
               or refuse if no chunk scores above it. Hallucination is worse than refusal.
```

These are not generic suggestions. They cite specific query IDs, specific values, and specific dates from your own data.

You apply what makes sense:

```bash
engram apply 4
engram reject 5 --notes "will revisit once we improve the other category docs"
```

---

### What you get after a few weeks

- Your system prompt evolves automatically with lessons grounded in real outcomes
- You have a prioritised backlog of evidence-backed improvements instead of guesswork
- You can see accuracy trends with `engram report` instead of digging through logs
- When something breaks, the diary tells you exactly which day and what changed

This is the difference between an AI system that degrades silently and one that compounds over time.

---

## Where else this makes sense

Engram works for any system where an AI makes a decision and you can eventually know if it was right:

- **RAG pipelines** — track which queries produce wrong answers and why
- **Content moderation** — track false positives/negatives, improve classifier prompts
- **AI agents** — track tool calls and their outcomes, improve routing logic
- **Recommendation systems** — track what was recommended vs. what was clicked
- **Document processing** — track extraction decisions vs. ground truth
- **Autonomous workflows** — track step-level decisions in multi-step pipelines

The pattern is always the same: observe → outcome → lessons → proposals → apply → repeat.

---

## Installation

```bash
pip install engram
```

Requires Python 3.11+ and an `ANTHROPIC_API_KEY` environment variable.

---

## Quick start

**1. Initialise a config file**

```bash
engram init
```

This creates `engram.yaml` in the current directory. Edit it to describe your system.

**2. Integrate with your code**

```python
from engram import Engram

eng = Engram("engram.yaml", db_path="engram.db")
eng.start()  # starts the background scheduler

# Record a decision wherever your system makes one
eng.observe(
    "req-001",
    decision="APPROVED",
    context={"model": "claude-sonnet-4-6", "confidence": 0.91, "category": "factual"},
)

# Later, push the outcome (binary strategy)
eng.outcome("req-001", "correct")

# Inject active lessons into your system prompt
lessons = eng.active_lessons()
system_prompt = base_prompt + "\n\nLessons:\n" + "\n".join(f"- {l}" for l in lessons)
```

**3. Review proposals**

```bash
engram review           # pending proposals
engram review --all     # including applied / rejected
```

**4. Apply or reject**

```bash
engram apply 3          # mark proposal 3 as applied
engram reject 5 --notes "not relevant for our setup"
```

**5. See a summary**

```bash
engram report           # last 7 days
engram report --days 30
```

---

## engram.yaml

```yaml
name: "QuestionAnsweringAgent"
domain: |
  An AI assistant that answers user questions using a RAG pipeline.
  Decisions are ANSWERED, REFUSED, or ESCALATED.
  Outcomes are labelled by human reviewers or inferred from user feedback.

  Key context fields: model, retrieval_score, confidence, query_category, latency_ms

outcome:
  strategy: binary   # binary | score | custom

parameters:
  - name: retrieval_score_threshold
    type: float
    description: "Minimum cosine similarity to include a chunk."

  - name: confidence_threshold
    type: float
    description: "Model confidence below which we escalate to a human."

proposal_categories:
  - prompt
  - retrieval
  - routing
  - cost
  - latency
  - architecture

schedule:
  diary_time: "23:00 UTC"
  propose_time: "01:00 UTC"
  checkpoint_day: sunday
  lesson_extraction: weekly   # weekly | on_error

llm:
  model: claude-sonnet-4-6
  max_tokens: 2000
```

### `domain`
The richer this description, the better the proposals. Describe what your system does, what decisions it makes, and what context fields are available. Engram passes this directly to the LLM when analysing your data.

### `outcome.strategy`

| Strategy | How it works |
|----------|-------------|
| `binary` | You call `eng.outcome(id, "correct"\|"wrong"\|"inconclusive")` explicitly |
| `score`  | Outcome is a numeric field already in context — set `score_field` and `score_threshold` |
| `custom` | Point to any callable with `assessor: "myapp.outcomes.my_fn"` |

### `parameters`
List the tunable knobs in your system. Engram uses these when generating proposals so it can suggest specific value changes rather than vague direction.

### `schedule`
Times are in `HH:MM Timezone` format. The scheduler runs as a daemon thread and fires within a 5-minute window of the configured time.

- **`diary_time`** — daily summary of decisions and outcomes
- **`propose_time`** — nightly proposal generation (runs after diary)
- **`checkpoint_day`** — weekly compression of diary entries into a single checkpoint
- **`lesson_extraction: on_error`** — triggers an extra extraction whenever 3+ wrong decisions happen in a day

---

## API reference

### `Engram(schema, db_path="engram.db")`
Initialise Engram. `schema` can be a file path, a dict, or an `EngramSchema` object.

### `eng.observe(decision_id, decision, context=None, ts=None)`
Record a decision. `decision_id` is your unique identifier. `decision` is the value (e.g. `"APPROVED"`, `"REFUSED"`). `context` is any dict of metadata.

### `eng.outcome(decision_id, result, metadata=None)`
Push an outcome. `result` is `"correct"`, `"wrong"`, or `"inconclusive"`.

### `eng.active_lessons() → list[str]`
Return the current active lessons. Inject these into your system prompts to close the learning loop.

### `eng.start()` / `eng.stop()`
Start or stop the background scheduler.

### Manual triggers
```python
eng.run_diary()       # write today's diary now
eng.run_proposals()   # run proposal generation now
eng.run_extract()     # run lesson extraction now
eng.run_checkpoint()  # run weekly checkpoint compression now
```

---

## CLI reference

```
engram init              Scaffold engram.yaml in the current directory
engram run               Start the scheduler as a blocking foreground process
engram review            List pending proposals
engram review --all      List all proposals including applied/rejected
engram apply <id>        Mark a proposal as applied
engram reject <id>       Reject a proposal
engram report            Show accuracy stats, lessons, and proposal summary
engram diary             Manually run the daily diary
engram propose           Manually run proposal generation
engram extract           Manually run lesson extraction
```

---

## Sidecar mode

If you want to run Engram as a separate process alongside your app:

```bash
engram run --schema engram.yaml --db engram.db
```

Your app still calls `eng.observe()` and `eng.outcome()` by pointing to the same `engram.db`. The scheduler process handles the rest.

---

## What gets stored

Engram uses a local SQLite database (`engram.db`) with three tables:

- **`decisions`** — every decision recorded via `observe()`, with outcome once known
- **`lessons`** — diary entries, extracted lessons, and weekly checkpoints (with TTL)
- **`proposals`** — improvement proposals with status (pending / applied / rejected)

Nothing leaves your machine except Anthropic API calls for diary analysis, lesson extraction, and proposal generation.
