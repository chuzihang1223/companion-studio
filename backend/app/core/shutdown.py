"""Close independent owned services even when one storage/device is unavailable."""

import inspect
import logging


async def close_service(callback, label):
    try:
        result = callback()
        if inspect.isawaitable(result):
            await result
        return True
    except Exception:
        # Fixed identifiers only. Provider messages can contain private paths or
        # credentials, and must not escape into the shared shutdown log.
        logging.getLogger(__name__).warning("service_shutdown_failed:%s", label)
        return False
