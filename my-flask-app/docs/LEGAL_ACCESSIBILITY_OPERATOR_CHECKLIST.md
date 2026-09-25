# Legal and accessibility operator release checklist

This checklist records decisions that cannot be established from source code. It is
not a public legal notice and does not represent a declaration of full compliance.

## Before production release

- Supply the operator's verified legal name, identifying number, address and legal
  contact details through the documented environment variables. Do not infer these
  values from a domain name.
- Determine with Israeli counsel whether the turnover-based accessibility exemption
  or any other exemption applies and whether an accessibility coordinator must be
  appointed. If one is appointed, configure only verified coordinator details.
- Arrange keyboard, 200%/400% zoom, narrow viewport and screen-reader testing of the
  production build. Automated checks are not a substitute for this review.
- Determine whether the database requires registration, or notification as a large
  database containing especially sensitive information, following Amendment 13.
- Determine whether the operator meets the threshold for appointing a data protection
  officer (DPO).
- Verify current data-processing/security agreements and transfer arrangements with
  Render/database hosting, Google OAuth/Gemini and PostHog (if enabled).
- Obtain final Israeli accessibility, privacy and consumer-law review.

## Analytics decision

Browser PostHog is optional and is not loaded until the visitor accepts the small
accessible control. Autocapture, session recording, automatic page-view collection,
persistent PostHog identity and person profiles are disabled. Only the pathname is
sent by the explicit page event. A reject choice is equally available and is stored
locally. `POSTHOG_SERVER_ENABLED` defaults to false; do not enable server events until
the operator has documented a lawful basis and a way to honor the visitor's choice.

## Payments / remote sales launch gate

No checkout, card, Stripe, PayPal, subscription, paid-report or other purchase route
was found in the active application. Before adding one, stop release until the flow
shows verified operator/business identification, address and contact details,
essential service characteristics, total price, payment and performance terms,
offer validity/restrictions, legally reviewed cancellation rights and process, and
an accessible written transaction confirmation. Configure a hard production check
for required business identity fields when payments are introduced.

## Environment configuration

- `LEGAL_OPERATOR_NAME`, `LEGAL_OPERATOR_ID`, `LEGAL_OPERATOR_ADDRESS`
- `LEGAL_CONTACT_EMAIL`, `ACCESSIBILITY_CONTACT_EMAIL`
- optional, only if actually appointed: `ACCESSIBILITY_COORDINATOR_NAME`,
  `ACCESSIBILITY_COORDINATOR_PHONE`
- `ACCESSIBILITY_REVIEW_DATE` (ISO `YYYY-MM-DD`)
- `POSTHOG_API_KEY`, `POSTHOG_HOST`; keep `POSTHOG_SERVER_ENABLED=false` unless the
  separate review described above is complete
- existing version/audit controls: `TERMS_VERSION`, `PRIVACY_VERSION`,
  `LEGAL_IP_HASH_SALT`
