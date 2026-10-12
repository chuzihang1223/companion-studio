import type {ButtonHTMLAttributes} from 'react'
import Icon,{type IconName} from './Icon'

export default function ComposerActionButton({icon,className='',...props}:ButtonHTMLAttributes<HTMLButtonElement>&{icon:IconName}){
 return <button {...props} type="button" className={'composer-action-button '+className}><span className="composer-action-icon"><Icon name={icon} size={18}/></span></button>
}
