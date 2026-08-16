"""
Plan-gating dependency for FastAPI routes.

Usage:
    @router.get("/premium", dependencies=[Depends(require_plan("pro"))])

The caller identifies themselves with an `X-User-Id` header. In a full app
this would come from a verified session/JWT; a header keeps the whole flow
(checkout -> webhook -> DB -> gated route) testable without building auth.
"""
from fastapi import Header, HTTPException
from app.database import get_subscription

PLAN_RANK = {"free": 0, "pro": 1, "elite": 2, "edge": 3}


def get_user_plan(user_id: str) -> str:
    sub = get_subscription(user_id)
    if not sub:
        return "free"
    return sub.get("plan", "free")


def require_plan(min_plan: str):
    min_rank = PLAN_RANK.get(min_plan, 0)

    def dependency(x_user_id: str = Header(default="anonymous", alias="X-User-Id")):
        user_plan = get_user_plan(x_user_id)
        if PLAN_RANK.get(user_plan, 0) < min_rank:
            raise HTTPException(
                status_code=403,
                detail=f"This feature requires the '{min_plan}' plan or higher. "
                f"Your current plan is '{user_plan}'.",
            )
        return {"user_id": x_user_id, "plan": user_plan}

    return dependency
