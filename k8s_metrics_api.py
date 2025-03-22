from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS
import os
import json
from kubernetes import client, config

app = Flask(__name__, static_url_path='')
CORS(app)  # Enable cross-origin requests

# Try to load in-cluster configuration
try:
    config.load_incluster_config()
    print("Running with in-cluster configuration")
except config.ConfigException:
    try:
        # Fall back to kubeconfig
        config.load_kube_config()
        print("Running with kubeconfig configuration")
    except config.ConfigException:
        print("Could not configure kubernetes client")

# Initialize Kubernetes API clients
v1 = client.CoreV1Api()
metrics_api = client.CustomObjectsApi()

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@app.route('/<path:path>')
def serve_static(path):
    return send_from_directory('static', path)

@app.route('/api/cluster-metrics')
def get_cluster_metrics():
    try:
        # Get nodes
        nodes = v1.list_node().items
        
        # Get node metrics
        node_metrics = metrics_api.list_cluster_custom_object(
            group="metrics.k8s.io",
            version="v1beta1",
            plural="nodes"
        )
        
        # Get pods
        pods = v1.list_pod_for_all_namespaces().items

        # Process and format the data
        formatted_data = {
            'nodes': [],
            'summary': {
                'totalCpuUsage': 0,
                'totalRamUsage': 0,
                'totalStorageUsage': 0,
                'totalNetworkUsage': 0,
                'nodesOnline': 0,
                'nodesTotal': len(nodes),
                'podsRunning': 0
            }
        }
        
        # Process node data
        for node in nodes:
            node_name = node.metadata.name
            node_status = 'active'
            
            # Check if node is ready
            for condition in node.status.conditions:
                if condition.type == 'Ready' and condition.status != 'True':
                    node_status = 'danger'
                    break
            
            # Find metrics for this node
            cpu_usage = 0
            ram_usage = 0
            cpu_capacity = 0
            memory_capacity = 0
            
            try:
                # Parse CPU and memory capacity
                cpu_capacity = float(node.status.capacity['cpu'])
                memory_str = node.status.capacity['memory']
                # Convert Ki to GB
                if memory_str.endswith('Ki'):
                    memory_capacity = int(memory_str[:-2]) / (1024 * 1024)
                elif memory_str.endswith('Mi'):
                    memory_capacity = int(memory_str[:-2]) / 1024
                elif memory_str.endswith('Gi'):
                    memory_capacity = int(memory_str[:-2])
                else:
                    memory_capacity = int(memory_str) / (1024 * 1024 * 1024)
                
                # Find node metrics
                for item in node_metrics['items']:
                    if item['metadata']['name'] == node_name:
                        # Parse CPU usage (typically in nanocores or millicores)
                        cpu_str = item['usage']['cpu']
                        if cpu_str.endswith('n'):
                            cpu_usage = int(cpu_str[:-1]) / 1000000000
                        elif cpu_str.endswith('m'):
                            cpu_usage = int(cpu_str[:-1]) / 1000
                        else:
                            cpu_usage = float(cpu_str)
                        
                        # Parse memory usage
                        mem_str = item['usage']['memory']
                        if mem_str.endswith('Ki'):
                            ram_usage = int(mem_str[:-2]) / (1024 * 1024)
                        elif mem_str.endswith('Mi'):
                            ram_usage = int(mem_str[:-2]) / 1024
                        elif mem_str.endswith('Gi'):
                            ram_usage = int(mem_str[:-2])
                        else:
                            ram_usage = int(mem_str) / (1024 * 1024 * 1024)
            except Exception as e:
                print(f"Error parsing metrics for node {node_name}: {e}")
            
            # Calculate percentages
            cpu_percent = min(round((cpu_usage / max(cpu_capacity, 0.1)) * 100), 100)
            ram_percent = min(round((ram_usage / max(memory_capacity, 0.1)) * 100), 100)
            
            # Storage metrics - estimate based on node information
            storage_total = 0
            storage_usage = 0
            
            try:
                # Check for the largest mountpoint capacity
                storage_total = 100  # Default placeholder
                storage_usage = 50   # Default placeholder
                
                # Ideally, you would use a DaemonSet with node-exporter to get accurate disk metrics
            except Exception as e:
                print(f"Error getting storage info for node {node_name}: {e}")
            
            # Network metrics - estimate based on node information
            network_bandwidth = 50  # Placeholder
            
            # Update summary data
            formatted_data['summary']['totalCpuUsage'] += cpu_percent
            formatted_data['summary']['totalRamUsage'] += ram_percent
            formatted_data['summary']['totalStorageUsage'] += storage_usage
            
            if node_status != 'danger':
                formatted_data['summary']['nodesOnline'] += 1
            
            # Determine warning or critical status based on usage
            if cpu_percent > 90 or ram_percent > 90:
                node_status = 'danger'
            elif cpu_percent > 80 or ram_percent > 80:
                node_status = 'warning'
            
            # Detect control plane nodes
            role = 'Worker'
            if node.metadata.labels and ('node-role.kubernetes.io/master' in node.metadata.labels or 
                                        'node-role.kubernetes.io/control-plane' in node.metadata.labels):
                role = 'Control Plane'
            
            # Get IP address
            ip_address = 'Unknown'
            for address in node.status.addresses:
                if address.type == 'InternalIP':
                    ip_address = address.address
                    break
            
            # Add node data
            node_info = {
                'name': node_name,
                'role': role,
                'status': node_status,
                'cpuCores': cpu_capacity,
                'cpuUsage': cpu_percent,
                'ramTotal': round(memory_capacity),
                'ramUsage': ram_percent,
                'storageTotal': storage_total,
                'storageUsage': storage_usage,
                'networkBandwidth': network_bandwidth,
                'ipAddress': ip_address,
                'kernel': node.status.node_info.kernel_version,
                'uptime': "N/A"  # Uptime would require additional calculations
            }
            
            formatted_data['nodes'].append(node_info)
        
        # Count running pods
        formatted_data['summary']['podsRunning'] = sum(1 for pod in pods if pod.status.phase == 'Running')
        
        # Average the summary metrics
        if formatted_data['summary']['nodesTotal'] > 0:
            formatted_data['summary']['totalCpuUsage'] = round(formatted_data['summary']['totalCpuUsage'] / formatted_data['summary']['nodesTotal'])
            formatted_data['summary']['totalRamUsage'] = round(formatted_data['summary']['totalRamUsage'] / formatted_data['summary']['nodesTotal'])
            formatted_data['summary']['totalStorageUsage'] = round(formatted_data['summary']['totalStorageUsage'] / formatted_data['summary']['nodesTotal'])
        
        # Network is cumulative
        formatted_data['summary']['totalNetworkUsage'] = network_bandwidth * formatted_data['summary']['nodesTotal']
        
        return jsonify(formatted_data)
    except Exception as e:
        print(f"Error fetching metrics: {e}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    # Use environment variable for port or default to 8080
    port = int(os.environ.get('PORT', 8080))
    # In production, use gunicorn instead
    app.run(host='0.0.0.0', port=port)