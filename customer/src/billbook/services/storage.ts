
import type {Session,User,UserData} from '../types';
export const SESSION_KEY='billbooks_session';
export const userKey=(id:string)=>`billbooks_user_${id}`;
export const usersKey='billbooks_users';
const clone=<T,>(v:T):T=>JSON.parse(JSON.stringify(v));
export const getSession=()=>{try{return JSON.parse(localStorage.getItem(SESSION_KEY)||'null') as Session|null}catch{return null}};
export const saveSession=(s:Session)=>localStorage.setItem(SESSION_KEY,JSON.stringify(s));
export const clearSession=()=>localStorage.removeItem(SESSION_KEY);
export const getUsers=():User[]=>JSON.parse(localStorage.getItem(usersKey)||'[]');
export const saveUsers=(u:User[])=>localStorage.setItem(usersKey,JSON.stringify(u));
export const getUserData=(id:string):UserData|null=>{try{return JSON.parse(localStorage.getItem(userKey(id))||'null')}catch{return null}};
export const saveUserData=(id:string,d:UserData)=>localStorage.setItem(userKey(id),JSON.stringify(d));
export const deepClone=clone;
export const browserInfo=()=>`${navigator.platform||'browser'} · ${navigator.userAgent.includes('Mobile')?'Mobile':'Desktop'}`;
export const uid=(prefix:string)=>`${prefix}_${Date.now()}_${Math.random().toString(36).slice(2,8)}`;
