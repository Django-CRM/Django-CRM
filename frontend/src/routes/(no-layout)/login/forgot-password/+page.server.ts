import axios from 'axios';
import { fail, redirect } from '@sveltejs/kit';
import * as env from '$app/env/private';
import * as publicEnv from '$app/env/public';
import { relayHeaders } from '#lib/server/relay.js';
import type { Actions } from './$types';
export const actions: Actions = {
 loginOtp: async (ctx) => start('login',ctx),
 reset: async (ctx) => start('reset',ctx)
};
async function start(flow:'login'|'reset',ctx:any) {
 const email=String((await ctx.request.formData()).get('email')??'').trim().toLowerCase();
 if(!email||!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return fail(400,{error:'Enter a valid email address.'});
 try {
  const r=await axios.post(`${publicEnv.PUBLIC_DJANGO_API_URL}/api/auth/recovery/request-code/`,{email,flow},{headers:{'Content-Type':'application/json',...relayHeaders(ctx)},timeout:15000});
  if(r.data.challenge_id){
   const opts={path:'/login',httpOnly:true,sameSite:'lax' as const,secure:env.NODE_ENV==='production',maxAge:600};
   ctx.cookies.set('recovery_email',email,opts);ctx.cookies.set('recovery_challenge',r.data.challenge_id,opts);
   throw redirect(303,flow==='login'?'/login/recovery-code':'/login/reset-password');
  }
  return {message:r.data.message};
 } catch(e) {if(e&&typeof e==='object'&&'status'in e)throw e;const msg=axios.isAxiosError(e)?e.response?.data?.error:null;return fail(axios.isAxiosError(e)&&e.response?.status===429?429:400,{error:typeof msg==='string'?msg:'We could not start recovery. Please try again.'});}
}
