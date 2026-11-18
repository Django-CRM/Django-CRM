import axios from 'axios';
import { fail, redirect, type Actions } from '@sveltejs/kit';
import * as env from '$app/env/private';
import * as publicEnv from '$app/env/public';
import { relayHeaders } from '#lib/server/relay.js';
import type { PageServerLoad } from './$types';
const opts=(secure:boolean)=>({path:'/',httpOnly:true,sameSite:'lax' as const,secure});
export const load:PageServerLoad=async({cookies})=>{const id=cookies.get('signup_challenge_id'),email=cookies.get('signup_email');if(!id||!email)throw redirect(303,'/create-account');return{email};};
export const actions:Actions={
 default:async({request,cookies,getClientAddress})=>{
  const id=cookies.get('signup_challenge_id'),f=await request.formData(),code=String(f.get('code')||'').trim();
  if(!id||!/^\\d{6}$/.test(code))return fail(400,{error:'Enter the six-digit activation code.'});
  try{
   const r=await axios.post(`${publicEnv.PUBLIC_DJANGO_API_URL}/api/auth/signup/verify-code/`,{challenge_id:id,code},{headers:{'Content-Type':'application/json',...relayHeaders({getClientAddress,request})},timeout:15000});
   const secure=env.NODE_ENV==='production';cookies.set('jwt_access',r.data.access_token,{...opts(secure),maxAge:86400});cookies.set('jwt_refresh',r.data.refresh_token,{...opts(secure),maxAge:31536000});
   cookies.delete('signup_challenge_id',{path:'/create-account'});cookies.delete('signup_email',{path:'/create-account'});
   throw redirect(303,'/org');
  }catch(e){if(e&&typeof e==='object'&&'status'in e)throw e;const x=e as{response?:{status?:number;data?:{error?:unknown}}};return fail(x.response?.status||400,{error:typeof x.response?.data?.error==='string'?x.response.data.error:'We could not verify your code. Try again.'});}
 },
 resend:async({cookies,getClientAddress,request})=>{
  const id=cookies.get('signup_challenge_id');if(!id)return fail(400,{error:'Your signup session expired. Start again.'});
  try{await axios.post(`${publicEnv.PUBLIC_DJANGO_API_URL}/api/auth/signup/resend-code/`,{challenge_id:id},{headers:{'Content-Type':'application/json',...relayHeaders({getClientAddress,request})},timeout:15000});return{resent:true};}
  catch(e){const x=e as{response?:{status?:number;data?:{error?:unknown}}};return fail(x.response?.status||400,{error:typeof x.response?.data?.error==='string'?x.response.data.error:'Could not send another code.'});}
 }
};
