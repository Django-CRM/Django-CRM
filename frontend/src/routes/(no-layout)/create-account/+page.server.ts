import axios from 'axios';
import { fail, redirect, type Actions } from '@sveltejs/kit';
import * as env from '$app/env/private';
import * as publicEnv from '$app/env/public';
import { relayHeaders } from '#lib/server/relay.js';
import type { PageServerLoad } from './$types';

const dial: Record<string,string> = { KE:'+254',UG:'+256',TZ:'+255',RW:'+250',NG:'+234',GH:'+233',ZA:'+27,ET:'+251,US:'+1',CA:'+1',GB:'+44',AU:'+61',NZ:'+64',IN:'+91',AE:'+971',SA:'+966',DE:'+49',FR:'+33',ES:'+34',IT:'+39',NL:'+31',SE:'+46',NO:'+47',DK:'+45',IE:'+353,PT:'+351,BR:'+55',MX:'+52',JP:'+81',CN:'+86',SG:'+65',MY:'+60',ID:'+62',PH:'+63',PK:'+92',EG:'+20',TR:'+90',PL:'+48' };
export const load: PageServerLoad = async ({cookies}) => { if(cookies.get('jwt_access')) throw redirect(303,'/org'); return {}; };
export const actions: Actions = {
 default: async ({request,cookies,getClientAddress}) => {
  const f=await request.formData(); const country=String(f.get('country')||'').toUpperCase();
  const raw=String(f.get('phone')||'').trim().replace(/[\\s().-]/g,'');
  const phone=raw.startsWith('+')?raw:`${dial[country]||''}${raw.replace(/^0+/,'')}`;
  const payload={first_name:String(f.get('first_name')||'').trim(),last_name:String(f.get('last_name')||'').trim(),email:String(f.get('email')||'').trim().toLowerCase(),phone,country,organization_name:String(f.get('organization_name')||'').trim(),job_title:String(f.get('job_title')||'').trim(),industry:String(f.get('industry')||'').trim(),employee_count:String(f.get('employee_count')||'').trim(),website:String(f.get('website')||'').trim(),business_description:String(f.get('business_description')||'').trim(),password:String(f.get('password')||''),confirm_password:String(f.get('confirm_password')||''),accept_terms:f.get('accept_terms')==='on',accept_privacy:f.get('accept_privacy')==='on',marketing_consent:f.get('marketing_consent')==='on'};
  try {
   const r=await axios.post<{ challenge_id: string; email: string }>(`${publicEnv.PUBLIC_DJANGO_API_URL}/api/auth/signup/request-code/`,payload,{headers:{'Content-Type':'application/json',...relayHeaders({getClientAddress,request})},timeout:15000});
   const secure=env.NODE_ENV==='production'; const opts={path:'/create-account',httpOnly:true,sameSite:'lax' as const,secure,maxAge:86400};
   cookies.set('signup_challenge_id',r.data.challenge_id,opts); cookies.set('signup_email',r.data.email||payload.email,opts);
   throw redirect(303,'/create-account/verify');
  } catch(e) {
   if(e && typeof e==='object' && 'status' in e) throw e;
   const x=e as {response?:{status?:number;data?:{error?:unknown}}};
   return fail(x.response?.status||400,{error:typeof x.response?.data?.error==='string'?x.response.data.error:'We could not start your registration. Check your details and try again.'});
  }
 }
};
