# -*- coding: utf-8 -*-
"""
Public routes blueprint - handles public-facing pages and authentication.
"""

import os
from flask import Blueprint, render_template, redirect, url_for, send_from_directory, session, flash, current_app
from flask_login import login_user, logout_user, current_user, login_required
from authlib.integrations.base_client.errors import MismatchingStateError

from app.extensions import db, oauth
from app.models import User
from app.data.israeli_car_market import israeli_car_market_full_compilation
from app.routes.helpers import api_ok, api_error, is_owner_user, get_redirect_uri, get_request_id

# Create blueprint
bp = Blueprint('public', __name__)


@bp.route('/healthz')
def healthz():
    return api_ok({"status": "ok"})


@bp.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(current_app.root_path, 'static'), 'favicon.ico')


@bp.route('/')
def index():
    return render_template(
        'index.html',
        car_models_data=israeli_car_market_full_compilation,
        user=current_user,
        is_owner=is_owner_user(),
        is_logged_in=current_user.is_authenticated,
    )


@bp.route('/login')
def login():
    for key in [k for k in session.keys() if k.startswith("google_oauth")]:
        session.pop(key, None)
    session.pop("authlib_oidc_nonce", None)
    redirect_uri = get_redirect_uri()
    return oauth.google.authorize_redirect(redirect_uri)


@bp.route('/auth')
def auth():
    try:
        token = oauth.google.authorize_access_token()
        userinfo = oauth.google.get('userinfo').json()
        user = User.query.filter_by(google_id=userinfo['id']).first()
        if not user:
            user = User(
                google_id=userinfo['id'],
                email=userinfo.get('email', ''),
                name=userinfo.get('name', '')
            )
            db.session.add(user)
            db.session.commit()
        login_user(user)
        return redirect(url_for('public.index'))
    except MismatchingStateError:
        current_app.logger.warning("[AUTH] mismatching_state request_id=%s", get_request_id())
        try:
            logout_user()
        except Exception:
            pass
        for key in [k for k in session.keys() if k.startswith("google_oauth")]:
            session.pop(key, None)
        flash("פג תוקף ההתחברות, אנא נסה שוב.", "error")
        return redirect(url_for('public.login'))
    except Exception:
        current_app.logger.exception("[AUTH] login failed request_id=%s", get_request_id())
        try:
            logout_user()
        except Exception:
            pass
        flash("שגיאת התחברות, נסה שוב מאוחר יותר.", "error")
        return redirect(url_for('public.index'))


@bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('public.index'))


@bp.route('/privacy')
def privacy():
    return render_template(
        'privacy.html',
        user=current_user,
        is_owner=is_owner_user(),
    )


@bp.route('/terms')
def terms():
    return render_template(
        'terms.html',
        user=current_user,
        is_owner=is_owner_user(),
    )


@bp.route('/coming-soon')
def coming_soon():
    return render_template(
        'coming_soon.html',
        user=current_user,
        is_owner=is_owner_user(),
    )
