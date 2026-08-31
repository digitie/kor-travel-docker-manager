import type { Metadata } from 'next';
import DashboardClient from '../components/DashboardClient';

export const metadata: Metadata = {
  title: 'Docker Manager UI',
  description: 'Docker 인프라 컨테이너 관리와 메트릭 운영 콘솔',
};

export default function Page() {
  return <DashboardClient />;
}
