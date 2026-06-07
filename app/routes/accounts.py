from fastapi import HTTPException, Path
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.models import Account
from app.serializers import public_utc_timestamp

async def get_account(
    account: str = Path(..., title="Account ID", description="The account ID"),
):
    # Check for URL-encoded special characters
    if "%" in account:
        raise HTTPException(
            status_code=400,
            detail="Account ID cannot contain URL-encoded special characters",
        )

    # Rest of the function remains the same
    try:
        # Assuming you have a function to get the account from the database
        account_data = await get_account_from_db(account)
        if account_data is None:
            return JSONResponse(
                content={"account": account, "exists": False, "balance_mrwk": "0"},
                media_type="application/json",
            )
        return JSONResponse(
            content={
                "account": account_data.account,
                "ledger_address": account_data.ledger_address,
                # Add other fields as needed
            },
            media_type="application/json",
        )
    except ValidationError as e:
        raise HTTPException(
            status_code=400,
            detail="Invalid account ID",
        )