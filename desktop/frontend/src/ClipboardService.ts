/** Desktop uses the native clipboard; browser preview uses its permission API. */
export const ClipboardService={async writeText(text:string){
 if(window.desktopAPI)return window.desktopAPI.clipboardWriteText(text)
 await navigator.clipboard.writeText(text);return true
}}
