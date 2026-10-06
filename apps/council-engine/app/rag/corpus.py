"""Synthetic Paytm-ecosystem knowledge base. No real Paytm content exists
in this project (fully simulated, same as the mock bank/CRM/payment-rail
clients) — this is a representative FAQ + policy set covering the kinds of
questions a payments-app support team actually handles, enough to exercise
real tier-1 (generic, retrievable) vs tier-2 (specific, needs reasoning)
routing.

`kind="faq"` entries are generic how-to questions RAG should answer
directly on a confident match. `kind="policy"` entries are NOT meant to be
served verbatim as an answer to a "how do I" question — they're grounding
context handed to the Council when a ticket is escalated (see
app/domains/support/nodes.py's CouncilResolutionNode), covering timelines
and rules the model should reason from rather than invent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class KnowledgeEntry:
    id: str
    kind: Literal["faq", "policy"]
    question: str
    answer: str
    tags: tuple[str, ...] = ()


CORPUS: tuple[KnowledgeEntry, ...] = (
    KnowledgeEntry(
        "faq-upi-payment",
        "faq",
        "How do I make a UPI payment?",
        "Open the app, tap Send Money or scan a QR code, enter the amount, and confirm with your UPI PIN. "
        "The payment settles instantly for most banks.",
        ("upi", "payment", "how-to"),
    ),
    KnowledgeEntry(
        "faq-add-beneficiary",
        "faq",
        "How do I add a bank account or beneficiary for NEFT/IMPS transfer?",
        "Go to Payments > Bank Transfer > Add Beneficiary, enter the recipient's account number, "
        "confirm it, and add the IFSC code. New beneficiaries are usually active for transfers within 30 minutes "
        "to 24 hours due to standard cooling-off security rules.",
        ("neft", "imps", "beneficiary", "bank-transfer", "how-to"),
    ),
    KnowledgeEntry(
        "faq-upi-pin-reset",
        "faq",
        "How do I reset or change my UPI PIN?",
        "Go to Profile > Bank Accounts > select the account > Reset UPI PIN. You'll need your debit card's "
        "last 6 digits and expiry date, plus an OTP sent to your registered mobile number.",
        ("upi", "pin", "reset", "how-to"),
    ),
    KnowledgeEntry(
        "faq-wallet-balance",
        "faq",
        "How do I check my wallet balance?",
        "Your wallet balance is shown on the home screen, or under Wallet > Balance. It updates in real time.",
        ("wallet", "balance", "how-to"),
    ),
    KnowledgeEntry(
        "faq-add-money-wallet",
        "faq",
        "How do I add money to my wallet?",
        "Go to Wallet > Add Money, choose a source (bank account, debit/credit card, UPI), enter the amount, "
        "and confirm. Funds are typically added instantly.",
        ("wallet", "add-money", "how-to"),
    ),
    KnowledgeEntry(
        "faq-daily-limits",
        "faq",
        "What are the daily transaction limits for UPI payments?",
        "Per NPCI guidelines, UPI transactions are capped at ₹1,00,000 per transaction and ₹1,00,000 total per "
        "day for most banks (some banks set lower limits). New payees may also have a ₹5,000 cap for the first "
        "24 hours as a fraud-prevention measure.",
        ("upi", "limits", "policy-ish-faq"),
    ),
    KnowledgeEntry(
        "faq-kyc",
        "faq",
        "How do I complete KYC verification?",
        "Go to Profile > KYC, upload a government ID (Aadhaar/PAN/passport) and a selfie, or visit a nearby "
        "KYC point for in-person verification. Full KYC removes wallet balance and transfer limits.",
        ("kyc", "verification", "how-to"),
    ),
    KnowledgeEntry(
        "faq-statement-download",
        "faq",
        "How do I download my transaction statement or passbook?",
        "Go to Wallet or Bank Account > Statement, choose a date range, and tap Download PDF. Statements are "
        "also emailed to your registered email address on request.",
        ("statement", "passbook", "how-to"),
    ),
    KnowledgeEntry(
        "faq-lost-card",
        "faq",
        "How do I block or report a lost debit card?",
        "Go to Cards > select the card > Block Card, or call the 24x7 support line immediately. Blocking "
        "takes effect instantly and a replacement card can be requested from the same screen.",
        ("card", "block", "lost", "how-to"),
    ),
    KnowledgeEntry(
        "faq-recharge-bill",
        "faq",
        "How do I pay a mobile recharge or utility bill?",
        "Go to Recharge & Bill Payments, select the biller category (mobile, electricity, DTH, etc.), enter "
        "your consumer/account number, and pay. Most billers confirm within a few minutes.",
        ("recharge", "bill-payment", "how-to"),
    ),
    KnowledgeEntry(
        "faq-mandate-autopay",
        "faq",
        "How do I set up or cancel an autopay mandate?",
        "Go to Payments > Mandates to view active autopay/subscription mandates. Tap a mandate to pause, "
        "modify the amount, or cancel it entirely — cancellation takes effect before the next billing cycle.",
        ("autopay", "mandate", "subscription", "how-to"),
    ),
    KnowledgeEntry(
        "faq-change-mobile-email",
        "faq",
        "How do I change my registered mobile number or email address?",
        "Go to Profile > Personal Details > Edit, enter the new number/email, and verify it with the OTP or "
        "link sent to it. Your old number stays valid until the new one is verified.",
        ("profile", "mobile-number", "email", "how-to"),
    ),
    KnowledgeEntry(
        "faq-cashback",
        "faq",
        "How do I redeem cashback or rewards?",
        "Cashback is usually credited automatically to your wallet within 24-48 hours of a qualifying "
        "transaction. Check Rewards > My Cashback for pending and credited amounts.",
        ("cashback", "rewards", "how-to"),
    ),
    KnowledgeEntry(
        "policy-debited-not-credited",
        "policy",
        "Money debited but transaction shows failed or isn't reflecting",
        "Per NPCI's UPI dispute resolution timelines: if a customer's account was debited but the merchant/"
        "recipient did not receive it (a 'debited but not credited' case), the amount is auto-reversed to the "
        "customer's source account within T+1 to T+5 business days. If not reversed after 5 business days, a "
        "formal dispute must be raised with the transaction's UTR/reference number, exact amount, and date — "
        "this requires looking up the specific transaction, which the automated system cannot verify on its "
        "own and should be escalated for manual investigation if the standard reversal window has already "
        "passed or the customer disputes the timeline.",
        ("dispute", "failed-transaction", "reversal", "policy"),
    ),
    KnowledgeEntry(
        "policy-dispute-process",
        "policy",
        "How to raise a dispute for a missing or failed transaction",
        "A dispute requires: the UTR/reference number, exact amount, date and time, and the recipient's "
        "UPI ID or account details. Disputes without a UTR number cannot be processed automatically and must "
        "be escalated to a human agent who can trace the transaction through the bank's backend.",
        ("dispute", "escalation", "policy"),
    ),
    KnowledgeEntry(
        "policy-escalation-criteria",
        "policy",
        "When a support ticket must be escalated to a human agent",
        "Escalate to a human agent when: the issue involves suspected fraud or unauthorized access, the "
        "disputed amount exceeds ₹10,000, the customer has raised the same issue more than once without "
        "resolution, the case requires looking up a specific customer's real transaction/account records that "
        "the automated assistant cannot access, or the customer explicitly asks for a human.",
        ("escalation", "policy", "fraud"),
    ),
    KnowledgeEntry(
        "policy-chargeback-timeline",
        "policy",
        "Chargeback and merchant dispute timelines",
        "Merchant chargebacks (e.g., paid-but-goods-not-received) are typically resolved within 7-21 business "
        "days depending on the merchant's response time to the dispute. The customer should be informed of "
        "this window and told a ticket has been raised, not given a guaranteed exact date.",
        ("chargeback", "merchant", "dispute", "policy"),
    ),
    KnowledgeEntry(
        "policy-wallet-kyc-limits",
        "policy",
        "Wallet balance and KYC limit tiers",
        "Minimum-KYC wallets are capped at ₹10,000 balance and ₹10,000/month spending. Full-KYC wallets have "
        "no balance cap and higher spending limits. A customer hitting a limit should be directed to complete "
        "full KYC rather than treated as a technical error.",
        ("kyc", "wallet", "limits", "policy"),
    ),
    # ---- second wave: broader product surface -----------------------------
    KnowledgeEntry(
        "faq-fastag-recharge",
        "faq",
        "How do I recharge my FASTag?",
        "Go to Recharge & Bill Payments > FASTag Recharge, select your bank/issuer, enter the vehicle number, "
        "and pay. Recharges usually reflect within 5-10 minutes; toll deductions can take a few hours to show.",
        ("fastag", "recharge", "how-to"),
    ),
    KnowledgeEntry(
        "faq-postpaid-bnpl",
        "faq",
        "How does Paytm Postpaid (buy now, pay later) work?",
        "Postpaid gives you a monthly credit line for payments; check eligibility under Postpaid in the app. "
        "The bill is generated on a fixed date each month and must be paid within the due date to avoid late fees "
        "and to keep the credit line active.",
        ("postpaid", "bnpl", "credit", "how-to"),
    ),
    KnowledgeEntry(
        "faq-multiple-bank-accounts",
        "faq",
        "Can I link multiple bank accounts and switch my default UPI account?",
        "Yes — go to Profile > Bank Accounts > Add New Bank Account to link more accounts, and tap 'Set as "
        "default' on any linked account to change which one your UPI payments debit from.",
        ("upi", "bank-account", "how-to"),
    ),
    KnowledgeEntry(
        "faq-security-lock",
        "faq",
        "How do I enable app lock or biometric security?",
        "Go to Profile > Security > App Lock, and choose PIN, fingerprint, or face unlock. This is separate "
        "from your UPI PIN and adds a layer of protection if your phone is unlocked by someone else.",
        ("security", "app-lock", "biometric", "how-to"),
    ),
    KnowledgeEntry(
        "faq-deactivate-account",
        "faq",
        "How do I deactivate or delete my account?",
        "Go to Profile > Settings > Deactivate Account. Any wallet balance must be transferred out first. "
        "Deactivation is reversible within 30 days by logging back in; after that it may require a support request.",
        ("account", "deactivate", "delete", "how-to"),
    ),
    KnowledgeEntry(
        "faq-update-pan-aadhaar",
        "faq",
        "How do I update my PAN or Aadhaar details?",
        "Go to Profile > KYC > Update Documents. Changing PAN/Aadhaar after verification may require re-KYC "
        "and can take 24-48 hours to reflect while it's re-verified.",
        ("kyc", "pan", "aadhaar", "how-to"),
    ),
    KnowledgeEntry(
        "faq-gift-voucher",
        "faq",
        "How do I redeem a gift voucher or promo code?",
        "Go to Rewards > Redeem Voucher, enter the code, and confirm. Vouchers are credited to your wallet or "
        "applied at checkout depending on the voucher type; each code can only be used once per account.",
        ("voucher", "promo-code", "rewards", "how-to"),
    ),
    KnowledgeEntry(
        "faq-ticket-booking-cancel",
        "faq",
        "How do I cancel a movie or travel ticket and get a refund?",
        "Go to Orders > My Tickets > select the booking > Cancel. Refund amount and eligibility depend on the "
        "operator's cancellation policy shown at booking time; refunds are usually processed within 5-7 business days.",
        ("tickets", "travel", "movies", "cancellation", "refund", "how-to"),
    ),
    KnowledgeEntry(
        "faq-paytm-money-investing",
        "faq",
        "How do I start investing in mutual funds or stocks?",
        "Open the Paytm Money section, complete the separate investment-account KYC (distinct from your wallet "
        "KYC), and choose Mutual Funds or Stocks to begin. Investment accounts are regulated separately from "
        "wallet/payment features.",
        ("investing", "mutual-funds", "stocks", "paytm-money", "how-to"),
    ),
    KnowledgeEntry(
        "faq-merchant-qr-payment-issue",
        "faq",
        "I scanned a merchant QR code but the payment didn't go through — what do I check first?",
        "Confirm the amount and merchant name shown before paying, check your internet connection, and retry. "
        "If money was debited without a success screen, wait a few minutes and check Transaction History — most "
        "such cases auto-resolve or auto-reverse; if it's still pending after that, it needs investigation.",
        ("qr-code", "merchant-payment", "failed-transaction", "how-to"),
    ),
    KnowledgeEntry(
        "faq-wallet-to-bank-transfer",
        "faq",
        "How do I transfer money from my wallet to my bank account?",
        "Go to Wallet > Transfer to Bank, enter the amount and select your linked bank account. Transfers above "
        "a small free monthly threshold may attract a nominal fee, shown before you confirm.",
        ("wallet", "bank-transfer", "how-to"),
    ),
    KnowledgeEntry(
        "policy-out-of-scope-requests",
        "policy",
        "Requests outside customer-support scope",
        "This assistant only handles Paytm account, payment, and transaction support. Requests for unrelated "
        "tasks (writing code, general trivia, essay writing) or for schemes to earn money / get free cashback "
        "outside official, documented offers are out of scope and must be declined rather than answered or "
        "escalated — escalating an out-of-scope request wastes a human agent's time on something support cannot "
        "help with anyway.",
        ("scope", "policy", "off-topic"),
    ),
    KnowledgeEntry(
        "policy-earning-schemes",
        "policy",
        "Requests about 'earning money' or 'making money fast' via the app",
        "Paytm does not offer schemes to 'earn' arbitrary amounts of money; legitimate ways users receive money "
        "are documented cashback/rewards on real transactions, the referral program, and (for eligible users) "
        "Paytm Money investments — all of which carry real terms and risk, not guaranteed income. A request "
        "phrased as wanting to 'earn ₹X' is almost always outside what support can respond to responsibly and "
        "should be redirected to the Rewards/Referral program pages rather than answered as a support ticket.",
        ("earning", "scheme", "rewards", "referral", "policy"),
    ),
    KnowledgeEntry(
        "policy-rbi-ombudsman-escalation",
        "policy",
        "Escalation path beyond internal support (RBI Ombudsman)",
        "If a complaint is not resolved within 30 days of being raised with support, the customer has the right "
        "to escalate to the RBI Banking Ombudsman / Integrated Ombudsman Scheme. Support should mention this "
        "path for unresolved, aged complaints rather than leaving the customer with no further recourse.",
        ("escalation", "rbi", "ombudsman", "policy"),
    ),
    KnowledgeEntry(
        "policy-repeat-complaint",
        "policy",
        "Handling a repeated complaint about the same issue",
        "If a customer indicates this is the second (or later) time raising the same issue, or references a "
        "prior ticket number, treat it as elevated priority and escalate to a human agent rather than re-running "
        "the standard automated resolution — a repeat complaint signals the automated path already failed once.",
        ("escalation", "repeat", "policy"),
    ),
)
