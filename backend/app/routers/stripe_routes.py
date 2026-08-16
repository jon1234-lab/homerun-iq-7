import json

import stripe
from fastapi import APIRouter, HTTPException, Request

from app.config import settings
from app.database import upsert_subscription, upsert_user
from app.schemas import CheckoutSessionRequest, CheckoutSessionResponse

router = APIRouter(prefix="/api/stripe", tags=["stripe"])

stripe.api_key = settings.STRIPE_SECRET_KEY
VALID_PLANS = {"pro", "elite", "edge"}


@router.post("/create-checkout-session", response_model=CheckoutSessionResponse)
def create_checkout_session(payload: CheckoutSessionRequest):
    if payload.plan not in VALID_PLANS:
        raise HTTPException(status_code=400, detail=f"Invalid plan '{payload.plan}'.")

    if not settings.STRIPE_SECRET_KEY:
        raise HTTPException(
            status_code=503,
            detail="Stripe isn't configured yet. Add STRIPE_SECRET_KEY and the plan price IDs "
            "to your backend environment to enable checkout.",
        )

    price_id = settings.PLAN_PRICE_IDS.get(payload.plan)
    if not price_id:
        raise HTTPException(
            status_code=503,
            detail=f"No Stripe price configured for '{payload.plan}'. "
            f"Set STRIPE_PRICE_{payload.plan.upper()}.",
        )

    upsert_user({"id": payload.user_id, "email": payload.email or ""})

    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=f"{settings.FRONTEND_URL}/upgrade?success=true&session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{settings.FRONTEND_URL}/upgrade?canceled=true",
            client_reference_id=payload.user_id,
            metadata={"user_id": payload.user_id, "plan": payload.plan},
            subscription_data={"metadata": {"user_id": payload.user_id, "plan": payload.plan}},
            customer_email=payload.email or None,
        )
    except stripe.error.StripeError as e:  # pragma: no cover
        raise HTTPException(status_code=400, detail=str(e))

    return CheckoutSessionResponse(checkout_url=session.url, session_id=session.id)


@router.post("/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    if settings.STRIPE_WEBHOOK_SECRET:
        try:
            event = stripe.Webhook.construct_event(payload, sig_header, settings.STRIPE_WEBHOOK_SECRET)
        except (stripe.error.SignatureVerificationError, ValueError) as e:
            raise HTTPException(status_code=400, detail=f"Signature verification failed: {e}")
    else:
        # Local dev before a webhook secret is configured. Use
        # `stripe listen --forward-to ...` and set STRIPE_WEBHOOK_SECRET for
        # real verification.
        try:
            event = json.loads(payload)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid webhook payload")

    event_type = event["type"] if isinstance(event, dict) else event.type
    obj = event["data"]["object"] if isinstance(event, dict) else event.data.object

    if event_type == "checkout.session.completed":
        metadata = obj.get("metadata") or {}
        user_id = obj.get("client_reference_id") or metadata.get("user_id")
        plan = metadata.get("plan", "free")
        if user_id:
            upsert_subscription(
                {
                    "user_id": user_id,
                    "plan": plan,
                    "stripe_customer_id": obj.get("customer"),
                    "stripe_subscription_id": obj.get("subscription"),
                    "status": "active",
                }
            )
            print(f"[stripe] activated plan '{plan}' for user {user_id}")

    elif event_type in ("customer.subscription.updated", "customer.subscription.deleted"):
        metadata = obj.get("metadata") or {}
        user_id = metadata.get("user_id")
        status = obj.get("status", "canceled")
        if user_id:
            downgraded = status in ("canceled", "unpaid", "incomplete_expired")
            upsert_subscription(
                {
                    "user_id": user_id,
                    "plan": "free" if downgraded else metadata.get("plan", "free"),
                    "stripe_customer_id": obj.get("customer"),
                    "stripe_subscription_id": obj.get("id"),
                    "status": "canceled" if downgraded else "active",
                }
            )

    return {"received": True}
