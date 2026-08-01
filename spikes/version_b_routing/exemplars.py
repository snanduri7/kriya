"""Hand-written natural-language exemplar phrases per in-scope command.

These are the "reference" points the classifier compares test input against -
not a test set themselves. Keep them short, varied in phrasing, and
representative of how someone would actually type the intent in prose rather
than as a command.
"""

EXEMPLARS = {
    "generate": [
        "add a health check endpoint to the user service",
        "create a REST endpoint that returns the current server time",
        "implement retry logic with exponential backoff for the http client",
        "build a caching layer in front of the database calls",
        "add unit tests for the payment processor",
        "write a script that parses the csv and uploads it to s3",
        "can you add pagination to the search results endpoint",
        "set up a CI workflow that runs the test suite on every push",
        "write a Dockerfile so this service can be containerized",
    ],
    "ask": [
        "why does the retry loop fail on timeout",
        "how does the dependency graph expansion work",
        "what does the vector store use for embeddings",
        "explain how the worktree gets reset between retries",
        "where is the egress policy enforced",
        "what happens when a skill is unverified",
        "how are the planner and architect agents different",
    ],
    "fix": [
        "here's a stack trace, can you fix it: NullPointerException at com.foo.Bar.baz",
        "the build is failing with a compilation error, please fix it",
        "tests are failing after the last change, fix them",
        "getting a connection refused error when starting the broker, fix this",
        "fix this traceback: ValueError: invalid literal for int()",
        "the app crashes on startup with a NoClassDefFoundError, can you fix that",
    ],
    "review": [
        "review my recent changes",
        "can you review the diff before I commit",
        "take a look at the code I just wrote and flag any issues",
        "review the changes in the workflow module",
        "check my latest commit for problems",
        "give me feedback on what I just changed",
    ],
    "analyze": [
        "what does this repo look like",
        "analyze the structure of this codebase",
        "give me an overview of the project",
        "what frameworks and dependencies does this repo use",
        "map out the modules in this codebase",
        "summarize the architecture of this project",
    ],
    "skills": [
        "what skills do you have for java",
        "list the skills kriya knows about",
        "show me the qpid skill",
        "do you have a skill for spring boot",
        "what rules does the ignite skill have",
        "which skills are verified right now",
    ],
}
