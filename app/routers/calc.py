"""Calc router — safe arithmetic evaluator.

Single source of truth: app/calc.py. AST-walked whitelist. Expected math
errors surface as 200-error payloads; anything unexpected is logged server-
side and returned as a generic InternalError (never a raw str(e) leak).
"""
from __future__ import annotations

import traceback

from fastapi import APIRouter, Depends, HTTPException, Request

from calc            import evaluate as _calc_evaluate
from dependencies    import get_rate_limiter, require_api_key
from logging_config  import getLogger
from request_models  import CalcRequest

_log = getLogger("usbai")

router = APIRouter()


@router.post("/api/calc")
async def api_calc(req: CalcRequest, request: Request,
                   _auth=Depends(require_api_key),
                   limiter=Depends(get_rate_limiter)):
    ip = request.client.host if request.client else "unknown"
    if not limiter.check(ip):
        raise HTTPException(429, "Rate limit exceeded; try again shortly.")
    try:
        result, result_str = _calc_evaluate(req.expression)
        return {"status": "ok", "expression": req.expression,
                "result": result, "result_str": result_str}
    except (ValueError, ZeroDivisionError, OverflowError) as e:
        # ponytail: expected math failures (div-by-zero, inf/nan, unsupported
        # op) are caller input problems -> 200-error payload, per AGENTS.md.
        return {"status": "error", "type": type(e).__name__,
                "message": f"Invalid expression: {e}"}
    except Exception as e:
        # ponytail: anything else is a bug in the evaluator itself — log full
        # traceback, return a generic payload. The old catch-all masked bugs
        # as "Invalid expression".
        _log.error(f"CALC unexpected {type(e).__name__}: {e}\n{traceback.format_exc()}")
        return {"status": "error", "type": "InternalError",
                "message": "Evaluation failed — see server logs"}
