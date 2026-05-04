import { serve } from "https://deno.land/std@0.168.0/http/server.ts"
import { createClient } from "https://esm.sh/@supabase/supabase-js@2"

const PINTEREST_API = "https://api.pinterest.com/v5"
const MAX_DESC = 500
const MAX_ALT  = 500

serve(async (req) => {
  if (req.method === 'OPTIONS') {
    return new Response('ok', { headers: { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Methods': 'POST', 'Access-Control-Allow-Headers': '*' } })
  }

  console.log("--- Post Pin Triggered ---")

  try {
    const { pin_id } = await req.json()
    console.log("Target Pin ID:", pin_id);
    
    const supabase = createClient(
      Deno.env.get('SUPABASE_URL') ?? '',
      Deno.env.get('SUPABASE_SERVICE_ROLE_KEY') ?? ''
    )

    console.log("Fetching tokens from database...");
    const { data: settings, error: setErr } = await supabase
      .from('settings').select('*').eq('id', 1).single()

    if (setErr || !settings) {
      console.error("Settings fetch error:", setErr);
      throw new Error('Pinterest tokens not found. Connect account in Settings first.');
    }

    const { data: pin, error: pinErr } = await supabase
      .from('pins').select('*').eq('id', pin_id).single()

    if (pinErr || !pin) {
      console.error("Pin fetch error:", pinErr);
      throw new Error('Pin not found');
    }

    const accessToken = settings.pinterest_access_token;
    // Per-pin board → settings fallback → env fallback
    const boardId = pin.board_id || settings.pinterest_board_id || Deno.env.get('PINTEREST_BOARD_ID');

    console.log("Payload Check:");
    console.log("- Token present:", !!accessToken);
    console.log("- Board ID:", boardId, pin.board_id ? "(from pin)" : "(from settings/env)");
    console.log("- Title:", pin.title);

    if (!accessToken || !boardId) {
      throw new Error('Missing Pinterest Access Token or Board ID');
    }

    // Description is sent as-is — already keyword-optimized by Gemini.
    // No hashtags — they're dead on Pinterest and can hurt SEO.
    const description = (pin.description || '').trim().slice(0, MAX_DESC)

    const payload: Record<string, unknown> = {
      board_id: boardId,
      title: pin.title.slice(0, 100),
      description: description,
      media_source: { source_type: "image_url", url: pin.image_url }
    }

    // Alt text for accessibility + SEO signal
    const altText = (pin.alt_text || '').trim()
    if (altText) {
      payload.alt_text = altText.slice(0, MAX_ALT)
    }

    // Only add link if present — Pinterest rejects empty link field
    if (pin.link_url) {
      payload.link = pin.link_url
    }

    console.log("Sending to Pinterest API...");
    console.log("- Description length:", description.length);
    console.log("- Alt text present:", !!altText);

    const resp = await fetch(`${PINTEREST_API}/pins`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${accessToken}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(payload)
    })

    const result = await resp.json()
    if (!resp.ok) {
      console.error("Pinterest API Error Response:", result);
      throw new Error(result.message || 'Pinterest API rejection');
    }

    console.log("Pin created successfully! Pinterest ID:", result.id);

    await supabase.from('pins').update({
      posted: true, pinterest_id: result.id, posted_at: new Date().toISOString()
    }).eq('id', pin_id)

    console.log("Database updated. Job done.");
    return new Response(JSON.stringify({ success: true, pinterest_id: result.id }), {
      headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' }
    })

  } catch (err) {
    console.error("Critical Post Error:", err.message);
    return new Response(JSON.stringify({ success: false, error: err.message }), {
      status: 400,
      headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' }
    })
  }
})
