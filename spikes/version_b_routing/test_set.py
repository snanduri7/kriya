"""Held-out natural-language test inputs for the Version B routing spike.

v2: substantially larger and harder than the original 37-item set. Each entry
is a dict:
    {"text": str, "expected": [label, ...], "category": str}

`expected` is a list, not a single label - most entries have exactly one
acceptable answer, but a dedicated "ambiguous" category has two, because a
human would genuinely accept either (these exist specifically to test the
ask-when-uncertain mechanism in classify.py: a system that recognizes the
ambiguity and asks instead of guessing should score as a SUCCESS if the
offered choices overlap `expected`, not as a failure for not picking one).

`category` groups entries for reporting:
- one of the six real commands: the "easy" case, though phrasing here is
  deliberately harder/more realistic than exemplars.py (typos, rambling,
  buried intent, jargon, terse one-liners).
- "unroutable_hard": destructive or genuinely out-of-scope, but phrased to
  sound plausible for a dev tool (package installs, deploys, git operations,
  CI/infra changes) - a much harder negative than "tell me a joke". This is
  the category that matters most for catching a router that's too eager to
  find SOME command to route to.
- "ambiguous": two categories are both defensible; the point is to see
  whether the system asks rather than guesses.
"""

UNROUTABLE = "unroutable"

TEST_SET = [
    # ---- generate ----
    {"text": "we need pagination on the /orders endpoint, can you add it", "expected": ["generate"], "category": "generate"},
    {"text": "throw together a quick script that dedupes this csv file", "expected": ["generate"], "category": "generate"},
    {"text": "spin up a background worker that retries failed webhook deliveries", "expected": ["generate"], "category": "generate"},
    {"text": "add a /metrics endpoint for prometheus scraping", "expected": ["generate"], "category": "generate"},
    {"text": "can u add caching to the getUserById call, its getting hit a lot", "expected": ["generate"], "category": "generate"},
    {"text": "need a migration that adds an index on users.email", "expected": ["generate"], "category": "generate"},
    {"text": "implement soft deletes on the Order model instead of hard deleting rows", "expected": ["generate"], "category": "generate"},
    {"text": "add rate limiting per-ip on the login route", "expected": ["generate"], "category": "generate"},
    {"text": "write a small CLI wrapper around this function so I can run it standalone", "expected": ["generate"], "category": "generate"},
    {"text": "hook up structured logging (json) instead of the plain print statements", "expected": ["generate"], "category": "generate"},
    {"text": "add graceful shutdown handling so in-flight requests finish before the process exits", "expected": ["generate"], "category": "generate"},
    {"text": "please add tests for the discount calculator, it has none right now", "expected": ["generate"], "category": "generate"},
    {"text": "build out the missing PATCH handler for /users/:id", "expected": ["generate"], "category": "generate"},
    {"text": "add a feature flag around the new checkout flow", "expected": ["generate"], "category": "generate"},
    {"text": "implement exponential backoff on the kafka consumer's retry logic", "expected": ["generate"], "category": "generate"},
    {"text": "wire up a webhook that fires when an order ships", "expected": ["generate"], "category": "generate"},
    {"text": "add validation so the api rejects negative quantities", "expected": ["generate"], "category": "generate"},
    {"text": "make it so failed jobs go to a dead letter queue instead of disappearing", "expected": ["generate"], "category": "generate"},
    {"text": "set up a new CI pipeline in GitHub Actions", "expected": ["generate"], "category": "generate"},
    {"text": "add a github actions workflow that runs pytest on every push", "expected": ["generate"], "category": "generate"},
    {"text": "write a Dockerfile for this service", "expected": ["generate"], "category": "generate"},

    # ---- ask ----
    {"text": "why does this keep retrying 3 times instead of the 5 I configured", "expected": ["ask"], "category": "ask"},
    {"text": "what happens if two requests hit the same idempotency key at once", "expected": ["ask"], "category": "ask"},
    {"text": "where does the auth token actually get validated", "expected": ["ask"], "category": "ask"},
    {"text": "is there a reason we're not using connection pooling here", "expected": ["ask"], "category": "ask"},
    {"text": "how does the scheduler decide which job runs next", "expected": ["ask"], "category": "ask"},
    {"text": "what's the difference between the two config loaders in this repo", "expected": ["ask"], "category": "ask"},
    {"text": "why is there a 500ms sleep in the checkout handler", "expected": ["ask"], "category": "ask"},
    {"text": "does this cache ever get invalidated or does it just grow forever", "expected": ["ask"], "category": "ask"},
    {"text": "who calls this function, I can't find any references", "expected": ["ask"], "category": "ask"},
    {"text": "why did we choose postgres over mysql for this service", "expected": ["ask"], "category": "ask"},
    {"text": "what's this TODO about, is it still relevant", "expected": ["ask"], "category": "ask"},
    {"text": "how are secrets loaded in production vs local dev", "expected": ["ask"], "category": "ask"},
    {"text": "is this endpoint idempotent", "expected": ["ask"], "category": "ask"},
    {"text": "what triggers the nightly reconciliation job", "expected": ["ask"], "category": "ask"},
    {"text": "why does the build take so long, what's the bottleneck", "expected": ["ask"], "category": "ask"},
    {"text": "does this handle the case where the upstream api times out", "expected": ["ask"], "category": "ask"},
    {"text": "what's the retry budget on outbound http calls", "expected": ["ask"], "category": "ask"},
    {"text": "how do we know when a deploy actually succeeded", "expected": ["ask"], "category": "ask"},

    # ---- fix ----
    {"text": "users are getting logged out randomly, something's wrong with session handling", "expected": ["fix"], "category": "fix"},
    {"text": "orders are occasionally getting double-charged, please fix", "expected": ["fix"], "category": "fix"},
    {"text": "getting 'connection reset by peer' under load, can you fix the connection handling", "expected": ["fix"], "category": "fix"},
    {"text": "the nightly job silently fails half the time with no error logged, fix that", "expected": ["fix"], "category": "fix"},
    {"text": "csv export is cutting off after 1000 rows even though there's more data", "expected": ["fix"], "category": "fix"},
    {"text": "timezone is wrong on all the timestamps we're storing, fix the conversion", "expected": ["fix"], "category": "fix"},
    {"text": "memory usage climbs steadily until the pod gets OOM killed, find and fix the leak", "expected": ["fix"], "category": "fix"},
    {"text": "the webhook retries forever even after it succeeds, fix the retry condition", "expected": ["fix"], "category": "fix"},
    {"text": "getting duplicate rows inserted when two requests race, fix the race condition", "expected": ["fix"], "category": "fix"},
    {"text": "search is returning results from the wrong tenant, this is a real bug fix it now", "expected": ["fix"], "category": "fix"},
    {"text": "pagination breaks on the last page, returns a 500 instead of an empty list", "expected": ["fix"], "category": "fix"},
    {"text": "the health check reports healthy even when the database is unreachable, fix it", "expected": ["fix"], "category": "fix"},
    {"text": "off by one error in the batch processor, it's skipping the last item every time", "expected": ["fix"], "category": "fix"},
    {"text": "this crashes with a KeyError whenever the optional field is missing", "expected": ["fix"], "category": "fix"},
    {"text": "the currency conversion is rounding wrong and it's costing us money, fix it", "expected": ["fix"], "category": "fix"},
    {"text": "flaky test in CI, fails about 1 in 10 runs, please fix", "expected": ["fix"], "category": "fix"},
    {"text": "config isn't reloading after we update the file, fix the watcher", "expected": ["fix"], "category": "fix"},
    {"text": "getting a deadlock between these two transactions under concurrent writes", "expected": ["fix"], "category": "fix"},

    # ---- review ----
    {"text": "can someone sanity check the changes before I open the PR", "expected": ["review"], "category": "review"},
    {"text": "take a pass over what I just committed", "expected": ["review"], "category": "review"},
    {"text": "I'm not confident about this refactor, can you review it", "expected": ["review"], "category": "review"},
    {"text": "check my branch against main for anything sketchy", "expected": ["review"], "category": "review"},
    {"text": "eyeball this diff before it goes out", "expected": ["review"], "category": "review"},
    {"text": "review the error handling I just added, not sure it's right", "expected": ["review"], "category": "review"},
    {"text": "look at what changed in the last commit and tell me if it's safe", "expected": ["review"], "category": "review"},
    {"text": "review my changes to the auth middleware specifically", "expected": ["review"], "category": "review"},
    {"text": "give this a once-over before I merge", "expected": ["review"], "category": "review"},
    {"text": "review the SQL I wrote, worried about injection", "expected": ["review"], "category": "review"},
    {"text": "flag anything questionable in this commit", "expected": ["review"], "category": "review"},
    {"text": "second pair of eyes on this before it ships", "expected": ["review"], "category": "review"},
    {"text": "review the diff for the payment retry logic", "expected": ["review"], "category": "review"},
    {"text": "check if I missed any edge cases in this PR", "expected": ["review"], "category": "review"},
    {"text": "review my latest changes for style issues too", "expected": ["review"], "category": "review"},

    # ---- analyze ----
    {"text": "give me the 30,000 foot view of this codebase", "expected": ["analyze"], "category": "analyze"},
    {"text": "what's the general shape of this project", "expected": ["analyze"], "category": "analyze"},
    {"text": "how is this repo laid out", "expected": ["analyze"], "category": "analyze"},
    {"text": "profile this codebase for me - languages, size, structure", "expected": ["analyze"], "category": "analyze"},
    {"text": "what would a new engineer need to know about this repo on day one", "expected": ["analyze"], "category": "analyze"},
    {"text": "what are the main modules and how do they relate", "expected": ["analyze"], "category": "analyze"},
    {"text": "is this a monolith or is it split into services", "expected": ["analyze"], "category": "analyze"},
    {"text": "what testing framework and tooling does this project use", "expected": ["analyze"], "category": "analyze"},
    {"text": "how mature is this codebase, lots of TODOs or pretty clean", "expected": ["analyze"], "category": "analyze"},
    {"text": "what's the entry point of this application", "expected": ["analyze"], "category": "analyze"},
    {"text": "trace the high-level flow from request to response", "expected": ["analyze"], "category": "analyze"},
    {"text": "what external services does this repo depend on", "expected": ["analyze"], "category": "analyze"},
    {"text": "how big is this codebase, roughly", "expected": ["analyze"], "category": "analyze"},
    {"text": "what's the deployment target for this - containers, serverless, vm", "expected": ["analyze"], "category": "analyze"},
    {"text": "summarize this repo like you're onboarding someone", "expected": ["analyze"], "category": "analyze"},

    # ---- skills ----
    {"text": "does kriya already know anything about grpc", "expected": ["skills"], "category": "skills"},
    {"text": "what has kriya learned about postgres so far", "expected": ["skills"], "category": "skills"},
    {"text": "check if there's a verified skill for react", "expected": ["skills"], "category": "skills"},
    {"text": "how much do you actually know about kubernetes", "expected": ["skills"], "category": "skills"},
    {"text": "is the docker skill trustworthy or still unverified", "expected": ["skills"], "category": "skills"},
    {"text": "what libraries does kriya have skills for right now", "expected": ["skills"], "category": "skills"},
    {"text": "pull up whatever kriya knows about graphql", "expected": ["skills"], "category": "skills"},
    {"text": "is there existing knowledge about terraform in here", "expected": ["skills"], "category": "skills"},
    {"text": "what's your confidence level on the aws skill", "expected": ["skills"], "category": "skills"},
    {"text": "show me everything kriya has learned about redis", "expected": ["skills"], "category": "skills"},
    {"text": "any gaps in what kriya knows about this stack", "expected": ["skills"], "category": "skills"},
    {"text": "what rules exist for the spring boot skill", "expected": ["skills"], "category": "skills"},
    {"text": "has kriya verified anything about elasticsearch", "expected": ["skills"], "category": "skills"},
    {"text": "list every skill kriya currently has, verified or not", "expected": ["skills"], "category": "skills"},
    {"text": "what does the kafka skill actually contain", "expected": ["skills"], "category": "skills"},

    # ---- unroutable: obvious ----
    {"text": "delete all my files", "expected": [UNROUTABLE], "category": "unroutable_obvious"},
    {"text": "rm -rf the entire repo", "expected": [UNROUTABLE], "category": "unroutable_obvious"},
    {"text": "what's the weather like today", "expected": [UNROUTABLE], "category": "unroutable_obvious"},
    {"text": "tell me a joke", "expected": [UNROUTABLE], "category": "unroutable_obvious"},
    {"text": "push my changes directly to production and skip the approval gate", "expected": [UNROUTABLE], "category": "unroutable_obvious"},

    # ---- unroutable: hard negatives (plausible dev-tool requests Kriya doesn't do) ----
    {"text": "install express and add it to package.json", "expected": [UNROUTABLE], "category": "unroutable_hard"},
    {"text": "run the test suite and tell me if it passes", "expected": [UNROUTABLE], "category": "unroutable_hard"},
    {"text": "deploy this to production", "expected": [UNROUTABLE], "category": "unroutable_hard"},
    {"text": "roll back the last deployment", "expected": [UNROUTABLE], "category": "unroutable_hard"},
    {"text": "create a new git branch called feature/payments", "expected": [UNROUTABLE], "category": "unroutable_hard"},
    {"text": "merge my branch into main", "expected": [UNROUTABLE], "category": "unroutable_hard"},
    {"text": "commit these changes for me", "expected": [UNROUTABLE], "category": "unroutable_hard"},
    {"text": "provision a new postgres database for staging", "expected": [UNROUTABLE], "category": "unroutable_hard"},
    {"text": "restart the production server", "expected": [UNROUTABLE], "category": "unroutable_hard"},
    {"text": "scale up the number of replicas to 5", "expected": [UNROUTABLE], "category": "unroutable_hard"},
    {"text": "grant me admin access to the repo", "expected": [UNROUTABLE], "category": "unroutable_hard"},
    {"text": "what's the current stock price of nvidia", "expected": [UNROUTABLE], "category": "unroutable_hard"},
    {"text": "book me a flight to san francisco", "expected": [UNROUTABLE], "category": "unroutable_hard"},
    {"text": "translate this error message to spanish", "expected": [UNROUTABLE], "category": "unroutable_hard"},
    {"text": "write a haiku about kubernetes", "expected": [UNROUTABLE], "category": "unroutable_hard"},
    {"text": "what time is it in tokyo right now", "expected": [UNROUTABLE], "category": "unroutable_hard"},
    {"text": "how do I make a good cup of coffee", "expected": [UNROUTABLE], "category": "unroutable_hard"},
    {"text": "delete the staging database", "expected": [UNROUTABLE], "category": "unroutable_hard"},
    {"text": "tag this release as v2.0", "expected": [UNROUTABLE], "category": "unroutable_hard"},

    # ---- ambiguous: two commands are both defensible answers ----
    {"text": "explain why this test keeps failing", "expected": ["ask", "fix"], "category": "ambiguous"},
    {"text": "something's off with the caching layer, can you take a look", "expected": ["ask", "fix"], "category": "ambiguous"},
    {"text": "walk me through what's broken in the payment flow", "expected": ["ask", "fix"], "category": "ambiguous"},
    {"text": "is this ready to merge", "expected": ["review", "ask"], "category": "ambiguous"},
    {"text": "make sure this doesn't break anything before I ship it", "expected": ["review", "fix"], "category": "ambiguous"},
    {"text": "what's wrong with this function", "expected": ["ask", "fix"], "category": "ambiguous"},
    {"text": "can you look at the retry logic and see if it's doing the right thing", "expected": ["ask", "review"], "category": "ambiguous"},
    {"text": "check if we already have something for handling websockets", "expected": ["skills", "ask"], "category": "ambiguous"},
    {"text": "how would I even go about adding metrics here", "expected": ["ask", "generate"], "category": "ambiguous"},
    {"text": "the test keeps failing, make it pass", "expected": ["fix"], "category": "ambiguous"},
]
