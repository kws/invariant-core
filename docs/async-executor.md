# Async Executor And Schedulers

This document describes `AsyncExecutor`, the opt-in asynchronous alternative to
the safe synchronous `Executor`.

`Executor` remains the conservative one-after-another implementation. Use it
when synchronous execution is enough or when a new op package has not been
audited for concurrent execution. Use `AsyncExecutor` when a caller wants an
awaitable API, concurrent independent node execution, or scheduler-controlled
placement across inline, thread, process, or future remote workers.

`AsyncExecutor` preserves the execution semantics defined in
[executor.md](./executor.md). It changes how ready ops are invoked, not what a
graph means.

## Basic Usage

```python
from invariant import AsyncExecutor, Node, OpRegistry, ref
from invariant.ops import stdlib
from invariant.store.memory import MemoryStore

registry = OpRegistry()
registry.register_package("stdlib", stdlib)
store = MemoryStore()

graph = {
    "a": Node(op_name="stdlib:identity", params={"value": "hello"}, deps=[]),
    "b": Node(op_name="stdlib:identity", params={"value": ref("a")}, deps=["a"]),
}

async with AsyncExecutor(registry, store) as executor:
    results = await executor.execute(graph, ["b"])
```

The default scheduler is `InlineScheduler`, so the default async executor is a
safe parity path. It still returns through `await`, but ops run inline unless a
different scheduler is supplied.

## Preserved Semantics

`AsyncExecutor` keeps the same graph behavior as `Executor`:

- callers request explicit output node IDs
- execution is demand-driven from those outputs
- inactive `SwitchNode` branches are not validated or executed
- `SubGraphNode` uses the same registry and store
- manifests, digests, and cache keys are unchanged
- `cache=False` bypasses cache lookup and cascades to downstream nodes
- ephemeral nodes are not single-flight deduplicated

The async executor may run independent ready nodes concurrently. It also adds
single-flight cache miss coordination for cacheable nodes: concurrent misses for
the same `(op_name, digest)` share one scheduler invocation.

Cache lookup and persistence remain parent-executor responsibilities. Schedulers
only invoke ops.

## Op Traits

Traits are portable op metadata used by schedulers. They are not lane names and
they are not part of artifact identity.

Built-in traits are exposed as a Python 3.10-compatible string enum:

```python
from invariant import OpTrait, op_traits


@op_traits(OpTrait.BLOCKING, OpTrait.IO_BOUND)
def fetch_resource(url: str) -> str:
    ...
```

The built-in traits are:

| Trait | Meaning |
|:--|:--|
| `blocking` | The op must not run inline on an async event loop if responsiveness matters. |
| `io-bound` | The op spends meaningful time waiting on I/O. |
| `cpu-bound` | The op spends meaningful time computing. |
| `thread-safe` | The op is safe to invoke concurrently in threads. |
| `process-safe` | The op can run in a separate process or remote worker when it has a worker-resolvable implementation reference. |

Traits are stored internally as `frozenset[str]`. Extension traits are allowed:

```python
registry.register(
    "deckr:fetch_image_url",
    fetch_image_url,
    traits={OpTrait.BLOCKING, OpTrait.IO_BOUND, "dev.deckr.resource-fetch"},
)
```

Unknown traits are preserved and ignored by schedulers that do not recognize
them.

## Registry Metadata

`OpRegistry.register()` accepts optional scheduler metadata:

```python
registry.register(
    "my:expensive_op",
    expensive_op,
    traits={OpTrait.CPU_BOUND, OpTrait.PROCESS_SAFE},
    implementation_ref="my_package.ops:expensive_op",
)
```

The registry exposes:

```python
binding = registry.get_binding("my:expensive_op")
traits = registry.traits("my:expensive_op")
implementation_ref = registry.implementation_ref("my:expensive_op")
```

`implementation_ref` uses `module.path:qualname` format. It is required for
process and remote execution because those schedulers must resolve the callable
inside the worker. The parent process callable is not the worker boundary.

For importable top-level functions, Invariant attempts to infer
`implementation_ref`. Lambdas, nested functions, local functions, and
`__main__` callables do not get inferred refs and cannot run in process or
remote schedulers unless the caller supplies an explicit ref.

## Schedulers

Schedulers implement the `InvocationScheduler` protocol:

```python
class InvocationScheduler(Protocol):
    async def invoke(self, request: InvocationRequest) -> Any:
        ...
```

`InvocationRequest` contains:

- `op_name`
- parent-process callable `op`
- resolved `manifest`
- normalized `traits`
- optional `implementation_ref`
- optional `cache_key`

Local scheduler implementations:

| Scheduler | Behavior |
|:--|:--|
| `InlineScheduler` | Invokes the op directly on the event loop thread. |
| `ThreadPoolScheduler` | Invokes the op through `ThreadPoolExecutor`. |
| `ProcessPoolScheduler` | Invokes a worker-resolvable op through `ProcessPoolExecutor`. |
| `RoutingScheduler` | Chooses another scheduler from traits and configured policy. |

Example routing setup:

```python
from invariant import (
    AsyncExecutor,
    ProcessPoolScheduler,
    RoutingScheduler,
    ThreadPoolScheduler,
)

scheduler = RoutingScheduler(
    thread_scheduler=ThreadPoolScheduler(max_workers=8),
    process_scheduler=ProcessPoolScheduler(max_workers=4),
)

async with AsyncExecutor(registry, store, scheduler=scheduler) as executor:
    results = await executor.execute(graph, ["output"])
```

Routing policy is conservative:

- `process-safe` goes to the configured process scheduler
- `blocking` or `io-bound` goes to the configured thread scheduler
- otherwise the request goes inline

If a `process-safe` op is routed to `ProcessPoolScheduler` without an
`implementation_ref`, execution fails clearly. The router does not silently
downgrade a process route.

## Process And Remote Worker Boundary

Process execution is a real Invariant boundary:

1. The parent executor builds the manifest and cache key.
2. `ProcessPoolScheduler` serializes the manifest with the Invariant store
   codec.
3. The worker receives `op_name`, `implementation_ref`, and serialized manifest
   bytes.
4. The worker runs normal op discovery for environment setup.
5. The worker imports the exact `implementation_ref`.
6. If discovery already bound `op_name` to a different implementation ref, the
   worker fails instead of clobbering or substituting by name.
7. The worker invokes the op, validates the result, serializes the artifact with
   the Invariant codec, and returns bytes.
8. The parent deserializes the artifact and stores it.

Do not rely on pickle for manifests, artifacts, or parent-process callables.
Python's process transport still moves a small task envelope, but Invariant data
crosses the worker boundary through Invariant serialization.

Remote schedulers such as Celery, NATS, or Ray should use the same logical
request shape. They should not require executor changes.

## Concurrency Guarantees And Limits

`AsyncExecutor` coordinates work inside one executor instance. Single-flight is
per instance, not a distributed lock.

Existing stores are still synchronous stores. The async executor protects its
own single-flight map, but it does not make a shared store globally atomic
across unrelated executors, event loops, processes, or hosts.

For distributed workers, use a scheduler-level or store-level coordination
strategy if cross-worker cache-miss deduplication is required.
