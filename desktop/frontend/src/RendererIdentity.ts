const identity=window.desktopAPI?.rendererContext().then(row=>row.renderer_id)
export async function rendererIdentity(){return await identity||null}
