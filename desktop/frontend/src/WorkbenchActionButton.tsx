import type { ButtonHTMLAttributes } from 'react';
export default function WorkbenchActionButton({
  className = '',
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button type="button" {...props} className={'workbench-action-button ' + className} />;
}
