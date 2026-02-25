from core.supabase_client import supabase

def get_zone_rule(zone_sigla: str, use_code: str):
    response = (
        supabase.table("zone_rules")
        .select("*")
        .eq("zone_sigla", zone_sigla)
        .eq("use_type_code", use_code)
        .limit(1)
        .execute()
    )

    if response.data and len(response.data) > 0:
        return response.data[0]

    return None
