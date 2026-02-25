from core.supabase_client import get_supabase

supabase = get_supabase()

def get_zone_rule(zone_sigla, use_type_code):
    response = (
        supabase
        .table("zone_rules")
        .select("*")
        .eq("zone_sigla", zone_sigla)
        .eq("use_type_code", use_type_code)
        .execute()
    )

    if response.data:
        return response.data[0]
    return None
