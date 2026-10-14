import type { ReactNode } from 'react';
import Icon, { type IconName } from './Icon';
export default function AppPanel({
  title,
  icon,
  children,
  className = '',
}: {
  title: string;
  icon: IconName;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`app-panel ${className}`}>
      <header>
        <Icon name={icon} />
        <h3>{title}</h3>
      </header>
      <div className="app-panel-body">{children}</div>
    </section>
  );
}
