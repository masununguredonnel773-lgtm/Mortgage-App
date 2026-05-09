import os
import json
import google.generativeai as genai
    api_key = os.environ.get("GOOGLE_API_KEY")
    if api_key:
        genai.configure(api_key=api_key)
        ai_model = genai.GenerativeModel('gemini-1.5-flash')
    else:
        ai_model = None
except Exception:
    ai_model = None

def calculate_self_employed_income(data):
    """
    Calculates qualifying monthly income for self-employed borrowers.
    """
    prev = data['annual_net_profit_prev_year']
    curr = data['annual_net_profit_current_year']
    
    if prev <= 0 or curr <= 0:
        return 0, "Warning: Negative or zero net profit reported."
    
    decline_ratio = (prev - curr) / prev if curr < prev else 0
    
    if curr >= prev:
        # Stable or increasing income
        qualifying_annual = (curr + prev) / 2
        note = "Stable/Increasing income: Using 2-year average."
    elif decline_ratio < 0.10:
        # Decline < 10%
        qualifying_annual = (curr + prev) / 2
        note = "Minor decline (<10%): Using 2-year average."
    elif decline_ratio <= 0.20:
        # Decline 10-20%
        qualifying_annual = curr
        note = "Moderate decline (10-20%): Using current (lower) year only."
    else:
        # Decline > 20%
        qualifying_annual = 0
        note = "Significant decline (>20%): Borrower may be ineligible unless stability is proven."
        
    return qualifying_annual / 12, note

def ai_verify(data):
    """
    Analyzes data for consistency and realism.
    Returns status and reasoning.
    """
    logs = []
    status = "Verified"
    
    # 1. DTI Outliers
    dti = data['dti_back']
    if dti < 5 or dti > 80:
        logs.append(f"DTI Alert: {dti:.2f}% is outside normal range (5%-80%).")
        status = "Warning"
    
    # 2. Credit Score Boundaries
    if data['credit_score'] in [300, 850]:
        logs.append(f"Credit Score Alert: Exact score {data['credit_score']} might be a placeholder.")
        status = "Warning"
    
    # 3. Property Value
    if data['property_value'] < 10000:
        logs.append(f"Property Value Alert: ${data['property_value']} is suspiciously low.")
        status = "Flagged"
    elif data['property_value'] > 100000000:
        logs.append(f"Property Value Alert: ${data['property_value']} is suspiciously high.")
        status = "Flagged"
    
    # 4. Income vs Savings
    if data['down_payment'] > data['monthly_income'] * 120:
        logs.append(f"Savings Alert: Down payment (${data['down_payment']}) seems very high relative to monthly income (${data['monthly_income']}).")
        if status != "Flagged":
            status = "Warning"

    # 5. Business Income Realism
    if data['is_self_employed']:
        if data['years_in_business'] < 2:
            logs.append(f"Continuity Alert: Only {data['years_in_business']} years in business. 2 years typically required.")
            status = "Warning"
        
        prev = data['annual_net_profit_prev_year']
        curr = data['annual_net_profit_current_year']
        if prev > 0 and curr < (prev * 0.8):
            logs.append("Trend Analysis: Current year profit is >20% lower than previous year.")
            if status != "Flagged":
                status = "Warning"

    # LLM Verification
    if ai_model:
        try:
            prompt = f"""
            Analyze the following mortgage assessment data for consistency and 'trueness' per Section 7 of requirements.
            
            Check for:
            1. Salary Benchmarking (7C): Is ${data['monthly_income']} realistic for a '{data['job_title']}'?
            2. Business Income Realism (7D): Check for industry-standard expense consistency (does profit seem too high for business type?) and trend stability (Trend Analysis).
            3. Savings Consistency (7A): Is the down payment (${data['down_payment']}) plausible given the income and debt history?
            4. Continuity (7D): Are the years in business ({data.get('years_in_business', 'N/A')}) sufficient (at least 2)?
            
            Data:
            - Client Name: {data['client_name']}
            - Job Title: {data['job_title']}
            - Is Self-Employed: {data['is_self_employed']}
            - Business Type: {data.get('business_type')}
            - Years in Business: {data.get('years_in_business')}
            - Annual Net Profit (Prev): ${data.get('annual_net_profit_prev_year')}
            - Annual Net Profit (Curr): ${data.get('annual_net_profit_current_year')}
            - Monthly Qualifying Income: ${data['monthly_income']}
            - Monthly Debt: ${data['monthly_debt']}
            - Credit Score: {data['credit_score']}
            - Property Value: ${data['property_value']}
            - Down Payment: ${data['down_payment']}
            - Property Type: {data.get('property_type')}
            
            Return ONLY a JSON object with:
            {{
                "status": "Verified" | "Warning" | "Flagged",
                "reasoning": "string explaining findings"
            }}
            """
            response = ai_model.generate_content(prompt)
            ai_result = json.loads(response.text.strip(' `json\n'))
            
            if ai_result['status'] == "Flagged":
                status = "Flagged"
            elif ai_result['status'] == "Warning" and status != "Flagged":
                status = "Warning"
            
            logs.append(f"AI Reasoning: {ai_result['reasoning']}")
        except Exception as e:
            logs.append(f"AI Verification error: {str(e)}")

    if not logs:
        logs.append("No consistency issues found in initial sweep.")
        
    return status, "\n".join(logs)

def check_eligibility(data):
    results = {"fha": False, "conventional": False, "notes": []}
    
    credit_score = data['credit_score']
    monthly_income = data['monthly_income']
    monthly_debt = data['monthly_debt']
    property_value = data['property_value']
    down_payment = data['down_payment']
    proposed_housing_payment = data['proposed_housing_payment']
    property_type = data['property_type']
    occupancy_type = data['occupancy_type']

    if monthly_income <= 0:
        results['notes'].append("Income is zero or negative. Ineligible.")
        return results

    # Calculated Metrics
    ltv = ((property_value - down_payment) / property_value) * 100
    dti_back = ((proposed_housing_payment + monthly_debt) / monthly_income) * 100
    dti_front = (proposed_housing_payment / monthly_income) * 100

    results['ltv'] = round(ltv, 2)
    results['dti_back'] = round(dti_back, 2)
    results['dti_front'] = round(dti_front, 2)

    # Conventional Check
    if credit_score >= 620:
        conv_ltv_limit = 97.0
        if property_type == "Multi-family":
            conv_ltv_limit = 85.0
            results['notes'].append("Conventional: Multi-family LTV limit is 85%.")
        elif property_type == "Manufactured":
            conv_ltv_limit = 95.0
            results['notes'].append("Conventional: Manufactured home LTV limit is 95% and HUD Tag is required.")
        
        if property_type == "Condo":
             results['notes'].append("Conventional: Condo requires HOA Project Review.")

        if occupancy_type != "Primary":
             results['notes'].append(f"Conventional: {occupancy_type} occupancy may require higher down payment.")

        if ltv <= conv_ltv_limit and dti_back <= 43:
            results['conventional'] = True
        elif ltv <= 80.0 and dti_back <= 50:
             results['conventional'] = True
             results['notes'].append("Conventional eligible via high DTI allowance (LTV <= 80)")
    else:
        results['notes'].append("Conventional: Credit score below 620.")

    # FHA Check
    if property_type == "Condo":
        results['notes'].append("FHA: Condo must be on FHA-Approved List.")
    elif property_type == "Manufactured":
        results['notes'].append("FHA: Manufactured home must be on a permanent foundation.")

    if occupancy_type != "Primary":
        results['notes'].append("FHA: Primary residence ONLY.")
    elif credit_score >= 580:
        if ltv <= 96.5 and dti_back <= 43:
            results['fha'] = True
        elif ltv <= 96.5 and dti_back <= 50:
            results['notes'].append("FHA: DTI between 43-50% may require manual underwriting.")
    elif credit_score >= 500:
        if ltv <= 90.0 and dti_back <= 43:
            results['fha'] = True
        else:
            if ltv > 90.0:
                results['notes'].append("FHA (Tier 2): LTV exceeds 90% for credit score < 580.")
    else:
        results['notes'].append("FHA: Credit score below 500.")
            
    return results

def get_officer_tips(results, inputs):
    tips_dict = {
        "Empathy & Empowerment": [
            "Frame the 'no' as a 'not yet'. Provide a clear action plan.",
            "Acknowledge that the home-buying process can be stressful."
        ],
        "Action Plan: Credit Improvement": [],
        "Action Plan: Debt Reduction": [],
        "Action Plan: LTV & Down Payment": [],
        "Documentation Checklist": [],
        "Next Steps": ["Set a specific date to re-evaluate in 90 days."]
    }

    if inputs.get('is_self_employed'):
        tips_dict["Documentation Checklist"] = [
            "Last 2 years of signed Personal and Business Federal Tax Returns.",
            "Year-to-Date (YTD) Profit & Loss (P&L) Statement.",
            "Balance Sheet."
        ]

    if not results['conventional'] and not results['fha']:
        for note in results['notes']:
            if "Credit score" in note:
                tips_dict["Action Plan: Credit Improvement"] = ["Review reports, pay down balances < 30%, ensure on-time payments."]
            if "DTI" in note or results['dti_back'] > 43:
                tips_dict["Action Plan: Debt Reduction"] = ["Pay off small debts, increase documented income, or lower home price."]
            if "LTV" in note or results['ltv'] > 90:
                tips_dict["Action Plan: LTV & Down Payment"] = ["Look into DPA programs, gift funds, or seller concessions."]
    elif not results['conventional'] or not results['fha']:
        tips_dict["Empathy & Empowerment"].append("Explain trade-offs between Conventional and FHA.")
    else:
        tips_dict = {"Success": ["Celebrate the win!", "Show side-by-side comparison of loan types."]}
    
    flat_tips = []
    for cat, items in tips_dict.items():
        if items: flat_tips.append(f"{cat}: {'; '.join(items)}")
    return tips_dict, " | ".join(flat_tips)
