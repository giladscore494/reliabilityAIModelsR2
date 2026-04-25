(function () {
    function getCSRFToken() {
        const meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.getAttribute('content') : '';
    }

    async function safeFetchJson(url, options = {}) {
        const headers = new Headers(options.headers || {});
        headers.set('Accept', 'application/json');
        if (options.body && !(options.body instanceof FormData) && !headers.has('Content-Type')) {
            headers.set('Content-Type', 'application/json');
        }
        const csrfToken = getCSRFToken();
        if (csrfToken && !headers.has('X-CSRF-Token')) {
            headers.set('X-CSRF-Token', csrfToken);
        }
        const response = await fetch(url, { credentials: 'include', ...options, headers });
        const payload = await response.json().catch(() => ({}));
        return {
            ok: response.ok && payload.ok !== false,
            status: response.status,
            payload,
            requestId: payload.request_id || response.headers.get('X-Request-ID') || null,
        };
    }

    function createClient(options = {}) {
        const modal = document.getElementById(options.modalId || 'researchConsentModal');
        const checkbox = document.getElementById(options.checkboxId || 'researchConsentCheckbox');
        const errorEl = document.getElementById(options.errorId || 'researchConsentError');
        const acceptButton = document.getElementById(options.buttonId || 'researchConsentAcceptButton');
        let accepted = options.accepted === true || (modal && modal.dataset.accepted === 'true');
        let consentId = options.consentId || null;
        let inFlight = false;
        let pendingResolver = null;

        function showError(message) {
            if (!errorEl) return;
            errorEl.textContent = message;
            errorEl.classList.remove('hidden');
        }

        function hideError() {
            if (errorEl) {
                errorEl.classList.add('hidden');
            }
        }

        function openModal() {
            if (!modal) return;
            hideError();
            if (typeof options.onConsentOpen === 'function') {
                try { options.onConsentOpen(); } catch (e) {}
            }
            modal.classList.remove('hidden');
            modal.classList.add('flex');
            document.body.classList.add('overflow-hidden');
        }

        function closeModal() {
            if (!modal) return;
            modal.classList.add('hidden');
            modal.classList.remove('flex');
            document.body.classList.remove('overflow-hidden');
        }

        async function acceptConsent(source) {
            if (inFlight) return false;
            if (!checkbox || !checkbox.checked) {
                showError('יש לסמן את תיבת האישור לפני המשך.');
                return false;
            }
            inFlight = true;
            hideError();
            if (acceptButton) acceptButton.disabled = true;
            try {
                const result = await safeFetchJson('/api/research/consent', {
                    method: 'POST',
                    body: JSON.stringify({
                        research_confirm: true,
                        accepted_source: source || options.defaultSource || 'web',
                    }),
                });
                if (!result.ok) {
                    showError((result.payload && result.payload.message) || 'לא הצלחנו לשמור את הסכמת המחקר.');
                    return false;
                }
                accepted = true;
                consentId = result.payload.consent_id || null;
                if (modal) modal.dataset.accepted = 'true';
                if (typeof options.onConsentAccepted === 'function') {
                    try { options.onConsentAccepted({ consentId }); } catch (e) {}
                }
                closeModal();
                return true;
            } catch (err) {
                showError('שגיאת רשת בעת שמירת הסכמת המחקר.');
                return false;
            } finally {
                inFlight = false;
                if (acceptButton) acceptButton.disabled = false;
            }
        }

        if (acceptButton) {
            acceptButton.addEventListener('click', async function () {
                const ok = await acceptConsent(options.defaultSource);
                if (ok && pendingResolver) {
                    const resolver = pendingResolver;
                    pendingResolver = null;
                    resolver(true);
                }
            });
        }

        if (checkbox) {
            checkbox.addEventListener('change', hideError);
        }

        return {
            get accepted() {
                return accepted;
            },
            get consentId() {
                return consentId;
            },
            async ensureConsent(source) {
                if (accepted) return true;
                openModal();
                return new Promise((resolve) => {
                    pendingResolver = resolve;
                    options.defaultSource = source || options.defaultSource;
                });
            },
            async saveResponses(payload) {
                const result = await safeFetchJson('/api/research/responses', {
                    method: 'POST',
                    body: JSON.stringify({
                        ...payload,
                        consent_id: payload.consent_id || consentId,
                    }),
                });
                if (result.ok && result.payload && result.payload.ok) {
                    return result.payload;
                }
                const message = (result.payload && result.payload.message) || 'לא הצלחנו לשמור את תשובות המחקר.';
                const error = new Error(message);
                error.requestId = result.requestId;
                throw error;
            },
        };
    }

    window.YedaResearch = {
        createClient,
    };
})();
