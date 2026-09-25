# Execution boundary

Scenario modules, assertion code, tool handlers, and graph factories are trusted Python code. Loading a scenario executes its module. Treat scenario files like other executable test code.

`DockerDriver` runs only the supplied agent script and the small JSONL client inside a trusted local Linux image. The world, tools, fixtures, and assertions remain in the host runner. The container has no network, no host credentials or Docker socket, read-only root filesystem and script mounts, an unprivileged user, dropped capabilities, no-new-privileges, and CPU/memory/PID limits. The driver resolves the image to its immutable local ID and records it. Images must be obtained explicitly; there is no fallback to unsandboxed execution if Docker is unavailable.

The Docker image itself is trusted. Container isolation depends on Docker and the host kernel; it is not a guarantee against a hostile image or kernel exploit. Host-side tool handlers must use simulated state. A handler intentionally written to contact production remains capable of doing so.

The container profile uses Docker's documented [none network driver](https://docs.docker.com/engine/network/drivers/none/) and [runtime restrictions](https://docs.docker.com/engine/containers/run/).

The protocol permits named tool calls and a final result, with bounded message sizes, unique request IDs, and a message limit. A deadline cancels the adapter and removes its container by name. Container tests probe egress, filesystem restrictions, tool routing, and cleanup. Enable with `AAE_DOCKER_TESTS=1` after pulling `python:3.11-slim`.

`ProcessDriver` supports trusted subprocess integrations and lifecycle testing, with a minimal environment. It is not a network/filesystem sandbox. `LangGraphDriver` and custom in-process drivers run trusted code in the host interpreter. Async deadlines are cooperative there: blocking Python code or code that suppresses cancellation can outlive the deadline. Use `DockerDriver` when an enforced agent boundary is required. Live hosted-model calls are currently demonstrated through trusted LangGraph; brokering model requests into the offline container is a separate future adapter task.

No runtime integration here is a production authorization system. The product tests policies against simulated effects; the deployed application's own tool authorization remains responsible for real actions.
