"""A saved task with structured output: books from one category of books.toscrape.com (a public demo shop made for
scraping practice), run with a variable, waited for, its history listed, then changed and deleted. Then one agent run
with an output schema.

    BOXLINE_API_KEY=bxl_… python examples/tasks.py
"""

from boxline import Boxline

BOOKS = {
    "type": "object",
    "properties": {
        "category": {"type": "string"},
        "books": {
            "type": "array",
            "minItems": 1,
            "maxItems": 3,
            "items": {"type": "object", "properties": {"title": {"type": "string"}, "price": {"type": "number"}}, "required": ["title", "price"]},
        },
    },
    "required": ["category", "books"],
}

with Boxline() as bx:
    task = bx.tasks.create(
        "Books by category",
        "Open https://books.toscrape.com, open the category %category% from the list on the left, and return the "
        "category's name and the first 3 books on its page with their prices in pounds (a number, without the sign).",
        variables=[{"name": "category", "default": "Travel", "description": "A category of the site"}],
        output=BOOKS,
        max_steps=15,
    )
    print(f"task {task['id']}: {task['name']}")
    try:
        started = bx.tasks.run(task["id"], variables={"category": "Poetry"})
        print(f"task run {started['id']} (agent run {started['runId']}): {started['status']}")
        done = bx.tasks.wait_for_run(started, timeout=600)
        print(f"{done['status']} in {(done['durationMs'] or 0) // 1000} s, ${done['usage']['costUsd']}: {done['resultText']}")
        for book in (done["result"] or {}).get("books", []):
            print(f"  {book['title']}: £{book['price']}")

        for r in bx.tasks.runs(task["id"], limit=10):
            print(f"history: {r['id']} {r['status']} {r['variables']}")

        changed = bx.tasks.update(task["id"], name="Books by category (3)", max_steps=None)
        print(f"renamed to {changed['name']!r}")
    finally:
        bx.tasks.delete(task["id"])
        print(f"deleted {task['id']}")

    # Structured output without a task.
    run = bx.agent.run(
        "Open https://books.toscrape.com and return the title and price (in pounds, a number) of the first book on the page.",
        output={"type": "object", "properties": {"title": {"type": "string"}, "price": {"type": "number"}}, "required": ["title", "price"]},
        max_steps=8,
    )
    answer = bx.agent.wait(run["id"])
    if answer["status"] == "completed":
        print(f"first book: {answer['result']['title']} at £{answer['result']['price']}")
    else:
        print(f"{answer['status']}: {answer.get('errorCode')} {answer['error']}")
